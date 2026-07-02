import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import azure.functions as func
from azure.data.tables import TableServiceClient
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from notion_client import Client

load_dotenv()

PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_DATABASE_URL = os.getenv("NOTION_DATABASE_URL", "")
TABLE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")

app = FastAPI()
function_app = func.AsgiFunctionApp(app=app, http_auth_level=func.AuthLevel.ANONYMOUS)

SESSION_TTL_SECONDS = 15 * 60  # 15 minutes
NOTION_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Persistent session store using Azure Table Storage
# Falls back to in-memory dict when Table Storage is unavailable
# (no Azurite required for local dev).
# ---------------------------------------------------------------------------
class SessionStore:
    """Persistent session store backed by Azure Table Storage.
    Falls back to an in-memory dict if Table Storage is unavailable
    (e.g. Azurite not running locally)."""

    TABLE_NAME = "leetcodejournalsessions"

    def __init__(self):
        self._memory: dict[str, dict] = {}
        try:
            self._client = TableServiceClient.from_connection_string(TABLE_CONNECTION_STRING)
            self._table = self._client.create_table_if_not_exists(self.TABLE_NAME)
            self._use_table = True
        except Exception:
            self._use_table = False

    def _row_key(self, user_id: str) -> str:
        return f"session_{user_id}"

    def _expired(self, entry: dict) -> bool:
        return time.time() - entry.get("_timestamp", 0) > SESSION_TTL_SECONDS

    def get(self, user_id: str) -> dict:
        if self._use_table:
            try:
                entity = self._table.get_entity(
                    partition_key="default", row_key=self._row_key(user_id)
                )
                if self._expired(entity):
                    self.delete(user_id)
                    return {}
                return json.loads(entity.get("data", "{}"))
            except Exception:
                pass
        entry = self._memory.get(user_id)
        if not entry or self._expired(entry):
            self._memory.pop(user_id, None)
            return {}
        return entry["data"]

    def set(self, user_id: str, session: dict) -> None:
        payload = json.dumps(session)
        if self._use_table:
            try:
                self._table.upsert_entity({
                    "PartitionKey": "default",
                    "RowKey": self._row_key(user_id),
                    "_timestamp": time.time(),
                    "data": payload,
                })
                return
            except Exception:
                pass
        self._memory[user_id] = {"_timestamp": time.time(), "data": session}

    def delete(self, user_id: str) -> None:
        if self._use_table:
            try:
                self._table.delete_entity("default", self._row_key(user_id))
            except Exception:
                pass
        self._memory.pop(user_id, None)


# Global session store (backed by Table Storage, with in-memory fallback)
_store = SessionStore()

JOURNAL_STEPS = [
    {"field": "problem",      "label": "What problem did you solve?",            "style": 1, "required": True},
    {"field": "difficulty",   "label": "Difficulty",                             "style": 0, "required": True},  # style 0 = dropdown (special)
    {"field": "topics",       "label": "Topics (comma separated)?",              "style": 1, "required": True},
    {"field": "leetcode_url", "label": "LeetCode URL?",                         "style": 1, "required": True},
    {"field": "code",         "label": "Paste your solution (use ``` for formatting):", "style": 2, "required": True},
    {"field": "reflection",   "label": "Your reflection?",                            "style": 2, "required": True},
]

TOTAL_STEPS = len(JOURNAL_STEPS)


def step_custom_id(user_id: str, step_index: int) -> str:
    """Encode user and step into a modal custom_id."""
    return f"lcj_s{step_index}_{user_id}"


def parse_lcj_custom_id(custom_id: str) -> tuple[str, str, int] | None:
    """Decode (user_id, kind, step_index) from custom_id like lcj_s0_id or lcj_c0_id."""
    if not custom_id.startswith("lcj_"):
        return None
    parts = custom_id.split("_", 2)
    if len(parts) != 3:
        return None
    kind = parts[1][0]  # 's' for step modal, 'c' for continue button
    try:
        step = int(parts[1][1:])
    except ValueError:
        return None
    return parts[2], kind, step


def _ensure_session(user_id: str) -> None:
    """Ensure a session dict exists for user (fetch or create from Table Storage)."""
    session = _store.get(user_id)
    if not session:
        _store.set(user_id, {})


def build_continue_button(user_id: str, next_step_index: int) -> JSONResponse:
    """Ephemeral message with a green button for the next step."""
    next_step = JOURNAL_STEPS[next_step_index]
    return JSONResponse({
        "type": 4,
        "data": {
            "content": f"✅ Got it!\n\n**Next: {next_step['label']}**",
            "flags": 64,
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 3,
                            "label": f"Step {next_step_index + 1} of {TOTAL_STEPS}",
                            "custom_id": f"lcj_c{next_step_index}_{user_id}",
                        }
                    ],
                }
            ],
        },
    })


def build_difficulty_select(user_id: str) -> JSONResponse:
    """Ephemeral message with a Difficulty dropdown."""
    return JSONResponse({
        "type": 4,
        "data": {
            "content": "✅ Got it! Select your difficulty:",
            "flags": 64,
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 3,
                            "custom_id": f"lcj_d1_{user_id}",
                            "min_values": 1,
                            "max_values": 1,
                            "placeholder": "Choose difficulty",
                            "options": [
                                {"label": "Easy",   "value": "Easy"},
                                {"label": "Medium", "value": "Medium"},
                                {"label": "Hard",   "value": "Hard"},
                            ],
                        }
                    ],
                }
            ],
        },
    })


def build_step_modal(user_id: str, step_index: int) -> JSONResponse:
    """Return a Discord modal for the given step."""
    step = JOURNAL_STEPS[step_index]
    return JSONResponse({
        "type": 9,
        "data": {
            "custom_id": step_custom_id(user_id, step_index),
            "title": f"LeetCode Journal ({step_index + 1}/{TOTAL_STEPS})",
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 4,
                            "custom_id": step["field"],
                            "label": step["label"],
                            "style": step["style"],
                            "required": step["required"],
                            "max_length": 4000,
                            # No prefill except at step 0 (keep it blank for steps)
                        }
                    ],
                }
            ],
        },
    })


def verify_discord_signature(request: Request, body: bytes) -> None:
    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")

    if not PUBLIC_KEY:
        raise HTTPException(status_code=500, detail="DISCORD_PUBLIC_KEY is not set")

    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Missing Discord signature headers")

    verify_key = VerifyKey(bytes.fromhex(PUBLIC_KEY))

    try:
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
    except BadSignatureError as exc:
        raise HTTPException(status_code=401, detail="Invalid Discord signature") from exc


def extract_modal_value(components: list[dict], custom_id: str) -> str | None:
    for row in components:
        for component in row.get("components", []):
            if component.get("custom_id") == custom_id:
                return component.get("value")
    return None


def build_notion_properties(session: dict[str, Any]) -> dict[str, Any]:
    date_value = datetime.now(timezone.utc).date().isoformat()

    def _text(content: str, **annotations: bool) -> dict:
        return {"type": "text", "text": {"content": content}, "annotations": annotations}

    topics_raw = session.get("topics", "")
    topics = [t.strip() for t in topics_raw.split(",") if t.strip()]

    return {
        "Name": {"title": [_text(session.get("problem", ""))]},
        "Date": {"date": {"start": date_value}},
        "Problem": {"rich_text": [_text(session.get("problem", ""))]},
        "Difficulty": {"select": {"name": session.get("difficulty", "")}},
        "Topics": {"multi_select": [{"name": t} for t in topics]},
        "LeetCode URL": {"url": session.get("leetcode_url", "")},
        "Code Snippet": {"rich_text": [_text(session.get("code", "")[:2000], code=True)]},
        "Reflection": {"rich_text": [_text(session.get("reflection", "")[:2000])]},
    }


def save_to_notion(session: dict) -> tuple[str, str]:
    """Returns (page_url, error_message). Retries on rate limits."""
    last_error = ""
    for attempt in range(1, NOTION_MAX_RETRIES + 1):
        try:
            notion = Client(auth=NOTION_TOKEN)
            page = notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties=build_notion_properties(session),
            )
            return page.get("url", ""), ""
        except Exception as exc:
            last_error = str(exc)
            if "rate" in last_error.lower():
                time.sleep(1 * attempt)
            else:
                break
    return "", last_error


def _preview(text: str, limit: int = 80) -> str:
    """Truncate long text for summary display."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def build_summary(user_id: str, session: dict) -> JSONResponse:
    code_preview = _preview(session.get("code", ""))
    reflection_preview = _preview(session.get("reflection", ""))
    lines = [
        "📋 **Review your entry:**",
        f"• **Problem:** {session.get('problem', '—')}",
        f"• **Difficulty:** {session.get('difficulty', '—')}",
        f"• **Topics:** {session.get('topics', '—')}",
        f"• **URL:** {session.get('leetcode_url', '—')}",
        f"• **Code:** {code_preview}",
        f"• **Reflection:** {reflection_preview}",
        "",
        "Click **Save** to write this to Notion, or **Cancel** to discard.",
    ]
    return JSONResponse({
        "type": 4,
        "data": {
            "content": "\n".join(lines),
            "flags": 64,
            "components": [{
                "type": 1,
                "components": [
                    {
                        "type": 2, "style": 3, "label": "Save to Notion",
                        "custom_id": f"lcj_v_{user_id}",
                    },
                    {
                        "type": 2, "style": 4, "label": "Cancel",
                        "custom_id": f"lcj_x_{user_id}",
                    },
                ],
            }],
        },
    })


@app.post("/interactions")
async def interactions(request: Request) -> JSONResponse:
    body = await request.body()
    verify_discord_signature(request, body)

    payload = json.loads(body)
    interaction_type = payload.get("type")

    # ── PING ──────────────────────────────────────────────────────────
    if interaction_type == 1:
        return JSONResponse({"type": 1})

    user_id = str(
        payload.get("member", {}).get("user", {}).get("id")
        or payload.get("user", {}).get("id")
        or ""
    )

    # ── SLASH COMMAND ─────────────────────────────────────────────────
    if interaction_type == 2:
        command_name = payload.get("data", {}).get("name", "")

        if command_name == "journal":
            _store.set(user_id, {})
            return build_step_modal(user_id, 0)

    # ── COMPONENT (button / select / summary confirm/cancel) ──────────
    if interaction_type == 3:
        custom_id = payload.get("data", {}).get("custom_id", "")

        # Summary: Save
        if custom_id.startswith("lcj_v_"):
            btn_user = custom_id.rsplit("_", 1)[-1]
            if btn_user != user_id:
                return JSONResponse({"type": 4, "data": {"content": "Not yours.", "flags": 64}})
            session = _store.get(user_id)
            _store.delete(user_id)
            if not session:
                return JSONResponse({"type": 4, "data": {"content": "No entry. /journal?", "flags": 64}})
            notion_url, error = save_to_notion(session)
            if error:
                return JSONResponse({"type": 4, "data": {"content": f"Notion error: {error}", "flags": 64}})
            content = "Saved your entry to Notion! 🎉"
            if notion_url:
                content += f"\n\n[Open in Notion]({notion_url})"
            if NOTION_DATABASE_URL:
                content += f"\n[View all entries]({NOTION_DATABASE_URL})"
            return JSONResponse({"type": 4, "data": {"content": content, "flags": 64}})

        # Summary: Cancel
        if custom_id.startswith("lcj_x_"):
            btn_user = custom_id.rsplit("_", 1)[-1]
            if btn_user == user_id:
                _store.delete(user_id)
            return JSONResponse({"type": 4, "data": {"content": "Entry discarded. /journal to start over.", "flags": 64}})

        parsed = parse_lcj_custom_id(custom_id)

        # Difficulty dropdown
        if parsed and parsed[1] == "d":
            menu_user_id, _, _step_index = parsed
            if menu_user_id != user_id:
                return JSONResponse({"type": 4, "data": {"content": "Not your menu.", "flags": 64}})
            selected = (payload.get("data", {}).get("values", []) or [""])[0]
            _ensure_session(user_id)
            session = _store.get(user_id)
            session["difficulty"] = selected
            _store.set(user_id, session)
            return build_continue_button(user_id, 2)

        # Continue button
        if parsed and parsed[1] == "c":
            btn_user_id, _, step_index = parsed
            if btn_user_id == user_id:
                _ensure_session(user_id)
                return build_step_modal(user_id, step_index)

        return JSONResponse({"type": 4, "data": {"content": "Unknown button. /journal?", "flags": 64}})

    # ── MODAL SUBMIT ──────────────────────────────────────────────────
    if interaction_type == 5:
        modal_id = payload.get("data", {}).get("custom_id", "")
        parsed = parse_lcj_custom_id(modal_id)

        if parsed is None or parsed[1] != "s":
            return JSONResponse({"type": 4, "data": {"content": "Unknown modal. /journal?", "flags": 64}})

        modal_user_id, _, step_index = parsed
        if modal_user_id != user_id:
            return JSONResponse({"type": 4, "data": {"content": "Not your modal.", "flags": 64}})

        _ensure_session(user_id)
        step = JOURNAL_STEPS[step_index]
        value = extract_modal_value(
            payload.get("data", {}).get("components", []),
            step["field"],
        )

        if not value and step["required"]:
            return JSONResponse({
                "type": 4,
                "data": {
                    "content": f"**{step['label']}** is required.",
                    "flags": 64,
                },
            })

        session = _store.get(user_id)
        session[step["field"]] = value
        _store.set(user_id, session)

        # Last step → show summary for confirmation
        if step_index + 1 >= TOTAL_STEPS:
            return build_summary(user_id, session)

        # Problem → difficulty dropdown
        if step_index == 0:
            return build_difficulty_select(user_id)

        # Other steps → continue button
        return build_continue_button(user_id, step_index + 1)

    return JSONResponse({
        "type": 4,
        "data": {"content": "Unsupported interaction type.", "flags": 64},
    })
