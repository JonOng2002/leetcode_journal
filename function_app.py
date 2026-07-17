import asyncio
import base64
import json
import os
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from httpx import Client as HttpxClient
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from notion_client import Client
from openai import OpenAI
import plotly.graph_objects as go

# ── Debug log ring buffer (last 100 entries, accessible via /debug/logs) ──
_INSTANCE_ID = uuid.uuid4().hex[:8]
_debug_logs: deque = deque(maxlen=100)


def _log(tag: str, msg: str) -> None:
    entry = f"[{datetime.now(timezone.utc).isoformat()}] [{_INSTANCE_ID}] {tag}: {msg}"
    _debug_logs.append(entry)

load_dotenv()

PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_DATABASE_URL = os.getenv("NOTION_DATABASE_URL", "")
DIGEST_USER_ID = os.getenv("DIGEST_USER_ID", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

app = FastAPI()

SESSION_TTL_SECONDS = 15 * 60  # 15 minutes
NOTION_MAX_RETRIES = 3

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_openai: OpenAI | None = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
_http = HttpxClient()

NEETCODE_TOPICS = [
    "Arrays & Hashing", "Two Pointers", "Stack", "Binary Search",
    "Sliding Window", "Linked List", "Trees", "Tries",
    "Heap / Priority Queue", "Intervals", "Greedy", "Advanced Graphs",
    "Backtracking", "Graphs", "1-D DP", "2-D DP", "Bit Manipulation",
    "Math & Geometry",
]

VALID_TOPICS = NEETCODE_TOPICS + [
    "DSA", "SQL",
    "Joins", "Aggregation", "Subqueries", "CTEs",
    "Window Functions", "Basic Queries",
]

VISION_PROMPT = """You are analyzing a LeetCode problem solution screenshot.

First detect whether this is a SQL problem or a DSA (general coding) problem.

Extract the following fields and return ONLY valid JSON (no markdown, no code fences):

{
  "problem": "The problem title (e.g. 'Two Sum')",
  "difficulty": "One of: Easy, Medium, Hard",
  "is_sql": true or false,
  "is_dsa": true or false,
  "topics": ["An array of topic tags. Exactly one of 'SQL' or 'DSA' must be included. For SQL problems, include 'SQL' plus sub-tags like Joins, Aggregation, Subqueries, CTEs, Window Functions, Basic Queries, Group-By. For DSA problems, include 'DSA' plus specific NeetCode tags from this list: Arrays & Hashing, Two Pointers, Stack, Binary Search, Sliding Window, Linked List, Trees, Tries, Heap / Priority Queue, Intervals, Greedy, Advanced Graphs, Backtracking, Graphs, 1-D DP, 2-D DP, Bit Manipulation, Math & Geometry."],
  "leetcode_url": "The LeetCode problem URL if visible, otherwise empty string",
  "code": "The code solution shown in the screenshot (preserve exact formatting)",
  "reflection": "A brief personal reflection (1-2 sentences) on what was learned or what was challenging"
}

Rules:
- Exactly one of is_sql or is_dsa must be true.
- is_sql and is_dsa cannot both be true.
- The topics array must always start with either 'SQL' or 'DSA'.
- Be thorough — check for the problem name in the page title, URL bar, or problem description."""


# ---------------------------------------------------------------------------
# Persistent session store using Azure Table Storage
# Falls back to in-memory dict when Table Storage is unavailable
# (no Azurite required for local dev).
# ---------------------------------------------------------------------------
class SessionStore:
    """In-memory session store with TTL expiry."""

    def __init__(self):
        self._memory: dict[str, dict] = {}

    def _expired(self, entry: dict) -> bool:
        return time.time() - entry.get("_timestamp", 0) > SESSION_TTL_SECONDS

    def get(self, user_id: str) -> dict:
        entry = self._memory.get(user_id)
        if not entry or self._expired(entry):
            self._memory.pop(user_id, None)
            return {}
        return entry["data"]

    def set(self, user_id: str, session: dict) -> None:
        self._memory[user_id] = {"_timestamp": time.time(), "data": session}

    def delete(self, user_id: str) -> None:
        self._memory.pop(user_id, None)


# ── Global session store (backed by Table Storage, with in-memory fallback) ──
_store = SessionStore()


# ── Shared log store (Table Storage backed, survives multi-instance) ──
class LogStore:
    def __init__(self):
        self._memory: deque = deque(maxlen=100)

    def write(self, tag: str, message: str) -> None:
        entry = f"[{datetime.now(timezone.utc).isoformat()}] [{_INSTANCE_ID}] {tag}: {message}"
        self._memory.append(entry)

    def recent(self, limit: int = 100) -> list[str]:
        return list(self._memory)[-limit:]


_log_store = LogStore()


def _log(tag: str, message: str) -> None:
    _log_store.write(tag, message)

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
    if isinstance(topics_raw, list):
        topics = topics_raw
    else:
        topics = [t.strip() for t in topics_raw.split(",") if t.strip()]

    return {
        "Name": {"title": [_text(session.get("problem", ""))]},
        "Date": {"date": {"start": date_value}},
        "Problem": {"rich_text": [_text(session.get("problem", ""))]},
        "Difficulty": {"select": {"name": session.get("difficulty", "")}},
        "Topics": {"multi_select": [{"name": t} for t in topics]},
        "LeetCode URL": {"url": session.get("leetcode_url") or None},
        "Code Snippet": {"rich_text": [_text(session.get("code", "")[:2000], code=True)]},
        "Reflection": {"rich_text": [_text(session.get("reflection", "")[:2000])]},
    }


def save_to_notion(session: dict) -> tuple[str, str]:
    """Returns (page_url, error_message). Retries on rate limits."""
    last_error = ""
    for attempt in range(1, NOTION_MAX_RETRIES + 1):
        try:
            notion = Client(auth=NOTION_TOKEN)
            props = build_notion_properties(session)
            _log("notion", f"save attempt {attempt}")
            page = notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties=props,
            )
            _log("notion", "save succeeded")
            return page.get("url", ""), ""
        except Exception as exc:
            last_error = str(exc)
            _log("notion", f"attempt {attempt} failed: {exc}")
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


# ---------------------------------------------------------------------------
# AI Vision pipeline
# ---------------------------------------------------------------------------

def _display_topics(topics: Any) -> str:
    if isinstance(topics, list):
        return ", ".join(topics)
    return str(topics or "")


def call_vision_api(image_bytes: bytes, content_type: str) -> dict:
    """Send image to GPT-4o mini and return extracted fields."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_uri = f"data:{content_type};base64,{b64}"

    if not _openai:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    response = _openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        max_tokens=2000,
    )
    return json.loads(response.choices[0].message.content)


def polish_reflection(user_thoughts: str, problem: str, difficulty: str) -> str:
    """Use AI to polish the user's rough reflection into a well-written one."""
    prompt = f"""Rewrite this personal LeetCode reflection into 1-2 well-written sentences.
Keep it natural and personal — like the user wrote it themselves, but clearer.

Problem: {problem}
Difficulty: {difficulty}
User's rough thoughts: {user_thoughts}

Return ONLY the polished reflection text, no JSON, no markdown, no quotes."""

    response = _openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
    )
    return response.choices[0].message.content.strip()


def _week_range() -> tuple[str, str]:
    """Return (monday_iso, sunday_iso) for the current ISO week."""
    today = datetime.now(timezone.utc)
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday.date().isoformat(), sunday.date().isoformat()


DIGEST_PROMPT = """You are a learning tracker assistant. Given this user's LeetCode/data problem data for the past week, write a short encouraging 1-2 paragraph summary.

This week's data:
{data}

Their personal reflections from each entry:
{reflections}

Write a friendly, motivational summary about their progress. Include specific numbers and reference what they learned or found challenging based on their reflections. Keep it concise."""


def build_weekly_digest() -> dict:
    """Query Notion for this week's entries and return a digest dict."""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    monday, sunday = _week_range()

    entries = []
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = _http.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=headers, json=body, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        entries.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    # Filter to this week
    week_entries = []
    for page in entries:
        date_prop = page.get("properties", {}).get("Date", {}).get("date")
        if not date_prop or not date_prop.get("start"):
            continue
        entry_date = date_prop["start"][:10]
        if monday <= entry_date <= sunday:
            week_entries.append(page)

    sql_count = 0
    dsa_count = 0
    sql_topics: Counter = Counter()
    dsa_topics: Counter = Counter()
    sql_by_diff: Counter = Counter()
    dsa_by_diff: Counter = Counter()
    reflections: list[dict] = []

    for page in week_entries:
        props = page.get("properties", {})
        topics = [t["name"] for t in props.get("Topics", {}).get("multi_select", []) if t.get("name")]
        diff = "Unknown"
        diff_prop = props.get("Difficulty", {}).get("select")
        if diff_prop and diff_prop.get("name") in ("Easy", "Medium", "Hard"):
            diff = diff_prop["name"]

        # Collect reflection
        ref_parts = props.get("Reflection", {}).get("rich_text", [])
        reflection_text = "".join(r.get("text", {}).get("content", "") for r in ref_parts if r.get("text"))
        if reflection_text:
            problem_name = props.get("Name", {}).get("title", [{}])[0].get("text", {}).get("content", "?")
            reflections.append({"problem": problem_name, "reflection": reflection_text[:300]})

        if "SQL" in topics:
            sql_count += 1
            for t in topics:
                if t != "SQL":
                    sql_topics[t] += 1
            sql_by_diff[diff] += 1
        else:
            dsa_count += 1
            for t in topics:
                if t != "DSA":
                    dsa_topics[t] += 1
            dsa_by_diff[diff] += 1

    return {
        "monday": monday,
        "sunday": sunday,
        "total": len(week_entries),
        "sql": {"count": sql_count, "topics": dict(sql_topics), "by_difficulty": dict(sql_by_diff)},
        "dsa": {"count": dsa_count, "topics": dict(dsa_topics), "by_difficulty": dict(dsa_by_diff)},
        "reflections": reflections,
    }


def format_digest_message(digest: dict) -> str:
    """Build a Discord-ready summary message from a digest dict."""
    lines = [f"📊 **Weekly Digest** ({digest['monday']} — {digest['sunday']})", ""]
    lines.append(f"**Total: {digest['total']} problems**")
    if digest['total'] == 0:
        lines.append("\nNo entries this week. Keep going! 💪")
        return "\n".join(lines)

    sql = digest['sql']
    dsa = digest['dsa']
    lines.append(f"   SQL: {sql['count']} | DSA: {dsa['count']}")
    lines.append("")

    if dsa['count']:
        lines.append("**DSA:**")
        topics = ", ".join(f"{t} ({c})" for t, c in sorted(dsa['topics'].items(), key=lambda x: -x[1]))
        if topics:
            lines.append(f"   Topics: {topics}")
        diff = ", ".join(f"{d}: {c}" for d, c in sorted(dsa['by_difficulty'].items()))
        if diff:
            lines.append(f"   Difficulty: {diff}")
        lines.append("")

    if sql['count']:
        lines.append("**SQL:**")
        topics = ", ".join(f"{t} ({c})" for t, c in sorted(sql['topics'].items(), key=lambda x: -x[1]))
        if topics:
            lines.append(f"   Topics: {topics}")
        diff = ", ".join(f"{d}: {c}" for d, c in sorted(sql['by_difficulty'].items()))
        if diff:
            lines.append(f"   Difficulty: {diff}")
        lines.append("")

    # AI summary
    if _openai and digest['total'] > 0:
        data_str = "\n".join(lines)
        refs = digest.get("reflections", [])
        ref_text = "\n".join(f"- {r['problem']}: {r['reflection']}" for r in refs) if refs else "None"
        try:
            summary = _openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": DIGEST_PROMPT.format(data=data_str, reflections=ref_text)}],
                max_tokens=500,
            ).choices[0].message.content.strip()
            lines.append("")
            lines.append(summary)
        except Exception as exc:
            _log("digest", f"ai summary failed: {exc}")

    return "\n".join(lines)


async def send_discord_dm(user_id: str, content: str) -> bool:
    """Send a DM to a Discord user using the bot token. Returns success."""
    if not DISCORD_BOT_TOKEN:
        _log("digest", "no DISCORD_BOT_TOKEN set, skipping DM")
        return False
    try:
        dm = _http.post(
            f"https://discord.com/api/v10/users/{user_id}/channels",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
            json={"recipient_id": user_id},
            timeout=10,
        )
        dm.raise_for_status()
        channel_id = dm.json().get("id")
        _http.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
            json={"content": content},
            timeout=10,
        )
        return True
    except Exception as exc:
        _log("digest", f"DM failed: {exc}")
        return False


async def process_vision_async(
    application_id: str, interaction_token: str, attachment_url: str, user_id: str
) -> None:
    """Background async task: download image, call AI, PATCH Discord webhook."""
    webhook_url = f"https://discord.com/api/v10/webhooks/{application_id}/{interaction_token}/messages/@original"
    _log("vision", f"start processing for user {user_id[:8]}...")
    try:
        resp = await asyncio.to_thread(_http.get, attachment_url, timeout=30)
        resp.raise_for_status()
        _log("vision", f"downloaded image ({len(resp.content)} bytes)")
        result = call_vision_api(resp.content, resp.headers.get("content-type", "image/png"))
        _log("vision", f"AI result: problem={result.get('problem')}, topics={result.get('topics')}")
        if isinstance(result.get("topics"), list):
            result["topics"] = ", ".join(result["topics"])
        _store.set(user_id, result)
        _log("vision", "session saved to store")
        data = build_vision_preview_data(user_id, result)
        await asyncio.to_thread(_http.patch, webhook_url, json=data, timeout=10)
        _log("vision", "discord message patched with preview")
    except Exception as exc:
        _log("vision", f"error: {type(exc).__name__}: {exc}")
        await asyncio.to_thread(
            _http.patch, webhook_url,
            json={"content": f"❌ Vision failed: {exc}", "flags": 64},
            timeout=10,
        )


def build_vision_preview_data(user_id: str, session: dict) -> dict:
    """Return data dict for the AI vision preview message."""
    code_preview = _preview(session.get("code", ""))
    reflection_preview = _preview(session.get("reflection", ""))
    topics = _display_topics(session.get("topics", ""))
    lines = [
        "🤖 **AI extracted from screenshot:**",
        f"• **Problem:** {session.get('problem', '—')}",
        f"• **Difficulty:** {session.get('difficulty', '—')}",
        f"• **Topics:** {topics}",
        f"• **URL:** {session.get('leetcode_url', '—')}",
        f"• **Code:** {code_preview}",
        f"• **Reflection:** {reflection_preview}",
        "",
        "Confirm or edit before saving:",
    ]
    return {
        "content": "\n".join(lines),
        "flags": 64,
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2, "style": 3, "label": "Confirm & Save",
                        "custom_id": f"lcj_ai_v_{user_id}",
                    },
                    {
                        "type": 2, "style": 1, "label": "Edit Topics",
                        "custom_id": f"lcj_ai_e_{user_id}",
                    },
                    {
                        "type": 2, "style": 1, "label": "Polish Reflection",
                        "custom_id": f"lcj_ai_p_{user_id}",
                    },
                    {
                        "type": 2, "style": 4, "label": "Cancel",
                        "custom_id": f"lcj_ai_x_{user_id}",
                    },
                ],
            }
        ],
    }


def build_vision_topic_modal(user_id: str, session: dict) -> JSONResponse:
    topics = _display_topics(session.get("topics", ""))
    return JSONResponse({
        "type": 9,
        "data": {
            "custom_id": f"lcj_ai_t_{user_id}",
            "title": "Edit Topics",
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 4,
                            "custom_id": "topics",
                            "label": "Topics (comma-separated)",
                            "style": 1,
                            "required": True,
                            "value": topics,
                            "max_length": 1000,
                        }
                    ],
                }
            ],
        },
    })


def build_reflection_modal(user_id: str, session: dict) -> JSONResponse:
    reflection = session.get("reflection", "")
    return JSONResponse({
        "type": 9,
        "data": {
            "custom_id": f"lcj_ai_r_{user_id}",
            "title": "Write Your Reflection",
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 4,
                            "custom_id": "reflection",
                            "label": "What did you learn? (1-2 sentences)",
                            "style": 2,
                            "required": True,
                            "value": reflection if isinstance(reflection, str) else "",
                            "max_length": 2000,
                        }
                    ],
                }
            ],
        },
    })


@app.get("/keepalive")
async def keepalive():
    """Pinged every 5 min by UptimeRobot to prevent cold starts."""
    _log("keepalive", "ping")
    return JSONResponse({"ok": True, "time": time.time()})


@app.get("/cron/digest")
async def cron_digest():
    """Called by cron-job.org every Sunday at noon. Sends weekly digest as DM."""
    _log("cron", "digest triggered")
    try:
        digest = build_weekly_digest()
        msg = format_digest_message(digest)
        if DIGEST_USER_ID and DISCORD_BOT_TOKEN:
            ok = await send_discord_dm(DIGEST_USER_ID, msg)
            _log("cron", f"DM sent: {ok}")
            return JSONResponse({"ok": ok, "total": digest["total"]})
        else:
            _log("cron", "DIGEST_USER_ID or DISCORD_BOT_TOKEN not set, returning JSON")
            return JSONResponse(digest)
    except Exception as exc:
        _log("cron", f"error: {exc}")
        return HTMLResponse(f"<h2>Error</h2><pre>{exc}</pre>", status_code=500)


@app.post("/interactions")
async def interactions(request: Request) -> JSONResponse:
    body = await request.body()
    payload = json.loads(body)
    _log("interaction", f"type={payload.get('type')} name={payload.get('data',{}).get('name','?')}")
    verify_discord_signature(request, body)

    interaction_type = payload.get("type")

    if interaction_type == 1:
        return JSONResponse({"type": 1})

    user_id = str(
        payload.get("member", {}).get("user", {}).get("id")
        or payload.get("user", {}).get("id")
        or ""
    )

    if interaction_type == 2:
        command_name = payload.get("data", {}).get("name", "")
        print(f"[interaction] slash command: /{command_name} by user {user_id}")
        _log("interaction", f"/{command_name} by user {user_id[:8]}...")

        if command_name == "ping":
            _log("ping", f"by user {user_id[:8]}...")
            return JSONResponse({
                "type": 4,
                "data": {"content": "🏓 Pong! Bot is alive.", "flags": 64},
            })

        if command_name == "digest":
            _log("digest", f"by user {user_id[:8]}...")
            HEADER = "\n".join([f"📊 **Weekly Digest** ({_week_range()[0]} — {_week_range()[1]})", ""])
            try:
                digest = build_weekly_digest()
                msg = format_digest_message(digest)
            except Exception as exc:
                _log("digest", f"build failed: {exc}")
                msg = f"{HEADER}\n❌ Error building digest: {exc}"
            return JSONResponse({
                "type": 4,
                "data": {"content": msg, "flags": 64},
            })

        if command_name == "journal":
            resolved = payload.get("data", {}).get("resolved", {})
            attachments = resolved.get("attachments", {})
            _log("journal", f"attachments: {list(attachments.keys()) if attachments else 'none'}")
            if attachments:
                _store.set(user_id, {})
                att = next(iter(attachments.values()))
                url = att.get("url") or att.get("proxy_url", "")
                asyncio.create_task(process_vision_async(
                    payload.get("application_id", ""),
                    payload.get("token", ""),
                    url,
                    user_id,
                ))
                return JSONResponse({
                    "type": 4,
                    "data": {
                        "content": "🔍 Analyzing your screenshot...",
                        "flags": 64,
                    },
                })
            _store.set(user_id, {})
            return build_step_modal(user_id, 0)

    # ── COMPONENT (button / select / summary confirm/cancel) ──────────
    if interaction_type == 3:
        custom_id = payload.get("data", {}).get("custom_id", "")

        # AI Vision: Confirm & Save
        if custom_id.startswith("lcj_ai_v_"):
            btn_user = custom_id.rsplit("_", 1)[-1]
            if btn_user != user_id:
                return JSONResponse({"type": 4, "data": {"content": "Not yours.", "flags": 64}})
            session = _store.get(user_id)
            _store.delete(user_id)
            if not session:
                return JSONResponse({"type": 4, "data": {"content": "No entry. /journal?", "flags": 64}})
            notion_url, error = save_to_notion(session)
            if error:
                short = error[:500] + "…" if len(error) > 500 else error
                return JSONResponse({"type": 4, "data": {"content": f"❌ Notion: {short}", "flags": 64}})
            _log("save", f"ai vision entry saved to notion")
            content = "Saved your entry to Notion! 🎉"
            if notion_url:
                content += f"\n\n[Open in Notion]({notion_url})"
            if NOTION_DATABASE_URL:
                content += f"\n[View all entries]({NOTION_DATABASE_URL})"
            return JSONResponse({"type": 4, "data": {"content": content, "flags": 64}})

        # AI Vision: Edit Topics (open modal)
        if custom_id.startswith("lcj_ai_e_"):
            btn_user = custom_id.rsplit("_", 1)[-1]
            if btn_user != user_id:
                return JSONResponse({"type": 4, "data": {"content": "Not yours.", "flags": 64}})
            session = _store.get(user_id)
            if not session:
                return JSONResponse({"type": 4, "data": {"content": "No entry. /journal?", "flags": 64}})
            return build_vision_topic_modal(user_id, session)

        # AI Vision: Cancel
        if custom_id.startswith("lcj_ai_x_"):
            btn_user = custom_id.rsplit("_", 1)[-1]
            if btn_user == user_id:
                _store.delete(user_id)
            return JSONResponse({"type": 4, "data": {"content": "Entry discarded. /journal to start over.", "flags": 64}})

        # AI Vision: Polish Reflection (open modal)
        if custom_id.startswith("lcj_ai_p_"):
            btn_user = custom_id.rsplit("_", 1)[-1]
            if btn_user != user_id:
                return JSONResponse({"type": 4, "data": {"content": "Not yours.", "flags": 64}})
            session = _store.get(user_id)
            if not session:
                return JSONResponse({"type": 4, "data": {"content": "No entry. /journal?", "flags": 64}})
            return build_reflection_modal(user_id, session)

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
                short = error[:500] + "…" if len(error) > 500 else error
                return JSONResponse({"type": 4, "data": {"content": f"❌ Notion: {short}", "flags": 64}})
            _log("save", "modal entry saved to notion")
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

        # AI Vision: Topic edit modal
        if modal_id.startswith("lcj_ai_t_"):
            modal_user = modal_id.rsplit("_", 1)[-1]
            if modal_user != user_id:
                return JSONResponse({"type": 4, "data": {"content": "Not yours.", "flags": 64}})
            session = _store.get(user_id)
            if not session:
                return JSONResponse({"type": 4, "data": {"content": "No entry. /journal?", "flags": 64}})
            value = extract_modal_value(
                payload.get("data", {}).get("components", []),
                "topics",
            )
            session["topics"] = value or ""
            _store.set(user_id, session)
            return JSONResponse({"type": 4, "data": build_vision_preview_data(user_id, session)})

        # AI Vision: Polish Reflection modal submit
        if modal_id.startswith("lcj_ai_r_"):
            modal_user = modal_id.rsplit("_", 1)[-1]
            if modal_user != user_id:
                return JSONResponse({"type": 4, "data": {"content": "Not yours.", "flags": 64}})
            session = _store.get(user_id)
            if not session:
                return JSONResponse({"type": 4, "data": {"content": "No entry. /journal?", "flags": 64}})
            raw = extract_modal_value(
                payload.get("data", {}).get("components", []),
                "reflection",
            )
            if not raw:
                return JSONResponse({"type": 4, "data": {"content": "Please write something first.", "flags": 64}})
            try:
                if _openai:
                    polished = await asyncio.to_thread(
                        polish_reflection,
                        raw,
                        session.get("problem", ""),
                        session.get("difficulty", ""),
                    )
                else:
                    polished = raw
            except Exception as exc:
                _log("polish", f"failed: {exc}")
                polished = raw
            session["reflection"] = polished
            _store.set(user_id, session)
            return JSONResponse({"type": 4, "data": build_vision_preview_data(user_id, session)})

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


# ---------------------------------------------------------------------------
# Dashboard — 3-layer Sankey with difficulty breakdown
# Layer 0: Category (DSA / SQL)
# Layer 1: Category + Difficulty (e.g. "DSA · Easy")
# Layer 2: Topic tags
# ---------------------------------------------------------------------------
@app.get("/debug/logs")
async def debug_logs():
    lines = _log_store.recent(100)
    _log("debug", f"viewed logs ({len(lines)} entries)")
    html = f"""<html><body>
<h2>Debug Logs (instance: {_INSTANCE_ID})</h2>
<pre>{"[no logs]" if not lines else chr(10).join(lines)}</pre>
</body></html>"""
    return HTMLResponse(html)


def _color(cat: str, difficulty: str, alpha: float = 1) -> str:
    """Return an rgba color string for a node."""
    palette = {
        ("DSA", "Easy"): (76, 175, 80),
        ("DSA", "Medium"): (255, 152, 0),
        ("DSA", "Hard"): (244, 67, 54),
        ("DSA", "Unknown"): (158, 158, 158),
        ("SQL", "Easy"): (33, 150, 243),
        ("SQL", "Medium"): (156, 39, 176),
        ("SQL", "Hard"): (233, 30, 99),
        ("SQL", "Unknown"): (158, 158, 158),
    }
    r, g, b = palette.get((cat, difficulty), (158, 158, 158))
    return f"rgba({r},{g},{b},{alpha})"


@app.get("/dashboard")
async def dashboard():
    try:
        if not NOTION_TOKEN:
            return HTMLResponse("<h1>NOTION_TOKEN not configured</h1>", status_code=500)

        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        # Fetch all pages with pagination
        entries = []
        cursor = None
        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            resp = _http.post(
                f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=headers, json=body, timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            entries.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        if not entries:
            return HTMLResponse("<h2>No entries found in the database yet.</h2>")

        # Count by (category, difficulty, topic)
        cd_topic_counts: Counter = Counter()     # (cat, diff, topic) -> count
        cd_problem_counts: Counter = Counter()   # (cat, diff) -> count of problems
        cat_counts: Counter = Counter()           # cat -> count

        for page in entries:
            props = page.get("properties", {})
            topics = [t["name"] for t in props.get("Topics", {}).get("multi_select", []) if t.get("name")]
            diff = "Unknown"
            diff_prop = props.get("Difficulty", {}).get("select")
            if diff_prop and diff_prop.get("name") in ("Easy", "Medium", "Hard"):
                diff = diff_prop["name"]

            cat = "DSA"
            if "SQL" in topics:
                cat = "SQL"

            cat_counts[cat] += 1
            cd_problem_counts[(cat, diff)] += 1

            for t in topics:
                if t not in ("SQL", "DSA"):
                    cd_topic_counts[(cat, diff, t)] += 1

        # Build node labels for 3 layers
        cats = ["DSA", "SQL"]
        diffs = ["Easy", "Medium", "Hard", "Unknown"]

        layer0 = cats                                           # categories
        layer1 = []                                              # cat · diff
        for c in cats:
            for d in diffs:
                if cd_problem_counts[(c, d)]:
                    layer1.append(f"{c} · {d}")

        layer2 = sorted({
            t for (c, d, t) in cd_topic_counts
        })

        all_labels = layer0 + layer1 + layer2
        l0_end = len(layer0)
        l1_end = l0_end + len(layer1)
        l2_end = l1_end + len(layer2)

        # Build node index lookup
        idx_of = {lbl: i for i, lbl in enumerate(all_labels)}

        # Build links: layer0 → layer1, layer1 → layer2
        sources: list[int] = []
        targets: list[int] = []
        values: list[int] = []
        link_colors: list[str] = []

        for c in cats:
            for d in diffs:
                key = (c, d)
                cnt = cd_problem_counts[key]
                if not cnt:
                    continue
                src = idx_of[c]
                dst = idx_of.get(f"{c} · {d}")
                if dst is None:
                    continue
                sources.append(src)
                targets.append(dst)
                values.append(cnt)
                link_colors.append(_color(c, d, 0.5))

                for t in layer2:
                    cnt2 = cd_topic_counts[(c, d, t)]
                    if not cnt2:
                        continue
                    sources.append(dst)
                    targets.append(idx_of[t])
                    values.append(cnt2)
                    link_colors.append(_color(c, d, 0.3))

        # Node colors
        node_colors = []
        for lbl in all_labels:
            if lbl in cats:
                node_colors.append(_color(lbl, "Unknown", 0.9))
            elif " · " in lbl:
                parts = lbl.split(" · ")
                node_colors.append(_color(parts[0], parts[1], 0.85))
            else:
                node_colors.append("rgba(158,158,158,0.8)")

        fig = go.Figure(data=[go.Sankey(
            arrangement="snap",
            node=dict(
                label=all_labels,
                color=node_colors,
                pad=15,
                thickness=20,
                line=dict(color="rgba(0,0,0,0.1)", width=1),
            ),
            link=dict(
                source=sources,
                target=targets,
                value=values,
                color=link_colors,
            ),
        )])

        dsa_total = cat_counts.get("DSA", 0)
        sql_total = cat_counts.get("SQL", 0)

        fig.update_layout(
            title=f"<b>Total: {dsa_total + sql_total} problems</b> — DSA: {dsa_total}, SQL: {sql_total}",
            font=dict(size=14, family="Arial"),
            height=600,
            margin=dict(l=10, r=10, t=60, b=10),
        )

        return HTMLResponse(fig.to_html(include_plotlyjs="cdn"))
    except Exception as exc:
        _log("dashboard", f"error: {type(exc).__name__}: {exc}")
        return HTMLResponse(f"<h2>Dashboard error</h2><pre>{exc}</pre>", status_code=500)


# ── Entrypoint for direct uvicorn run (Railway) ──
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
