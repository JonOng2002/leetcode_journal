# LeetCode Journal — Agent Guide

> **Important: Never push to GitHub without explicit user permission.** Stage and show the diff to the user first.

Single-file FastAPI app (Python) acting as a Discord bot. Collects LeetCode reflections via Discord modals OR screenshot → AI vision → Notion. Hosted on Railway (not Azure Functions).

## Key Files

| File | Purpose |
|---|---|
| `function_app.py` | Entire app in one file (Python v2, no `function.json`) |
| `cron_runner.py` | Standalone entrypoint for Railway cron job (weekly digest) |
| `PLANS.md` | Future features roadmap |
| `railway.json` | Railway build configuration |
| `config/.env.example` | Required env vars template |
| `config/local.settings.example.json` | Azure Functions local config template |

## Discord Interactions

| Type | Handler |
|---|---|
| PING (1) | `{"type": 1}` |
| SLASH_COMMAND (2) | `/journal [screenshot?]` |
| COMPONENT (3) | Buttons, selects, confirm/cancel |
| MODAL_SUBMIT (5) | Collect fields, edit topics |

## Slash Command Flow

`/journal` has one optional `screenshot` attachment param (type 11):

- **With screenshot**: Downloads image → GPT-4o mini → extracts fields → shows preview with Confirm / Edit Topics / Polish Reflection / Cancel buttons. Runs in a background task (`asyncio.create_task`). Initial response is a loading message, then the background task patches the Discord webhook with results.
- **Without screenshot**: Existing 6-step modal chain (Problem → Difficulty dropdown → Topics → URL → Code → Reflection → Save).

## Session Store (`SessionStore`)

- In-memory dict with 15-minute TTL. Previously backed by Azure Table Storage, simplified after migrating to Railway (single-container, no multi-instance issues).
- Expired sessions silently deleted on access.

## Custom ID Scheme

- `lcj_s{step}_{uid}` — step modal
- `lcj_c{step}_{uid}` — continue button
- `lcj_d{step}_{uid}` — difficulty dropdown
- `lcj_v_{uid}` — save to Notion (modal summary)
- `lcj_x_{uid}` — cancel (modal summary)
- `lcj_ai_v_{uid}` — confirm AI vision entry
- `lcj_ai_e_{uid}` — edit AI vision topics
- `lcj_ai_x_{uid}` — cancel AI vision entry
- `lcj_ai_t_{uid}` — AI vision topic edit modal submit
- `lcj_ai_p_{uid}` — AI vision polish reflection button
- `lcj_ai_r_{uid}` — AI vision reflection edit modal submit

## AI Vision

- Provider: OpenAI GPT-4o mini (configurable via `OPENAI_API_KEY`)
- Prompt extracts: `problem`, `difficulty`, `topics`, `leetcode_url`, `code`, `is_sql`, `is_dsa`, `reflection`
- Topics classified into fixed NeetCode DSA tags list (see `NEETCODE_TOPICS` in code). SQL sub-tags also supported: Joins, Aggregation, Subqueries, CTEs, Window Functions, Basic Queries.
- SQL problems detected by `is_sql` flag, DSA by `is_dsa` flag (exactly one must be true).
- Preview shows all extracted fields with Confirm/Edit Topics/Polish Reflection/Cancel buttons.
- Edit Topics opens a modal pre-filled with current tags.
- Polish Reflection opens a modal for rough thoughts, AI rewrites into polished reflection.

## Dashboard

`GET /dashboard` — queries all Notion DB entries → aggregates by SQL vs DSA topics → renders interactive Plotly Sankey diagram as HTML with 3-layer breakdown (category → difficulty → topic).

## Architecture Notes

- Hosted on **Railway** (not Azure Functions). `railway.json` uses nixpacks builder.
- `cron_runner.py` is a standalone entrypoint for Railway cron job (weekly digest DM).
- `agent.md` is in `.gitignore` and `.funcignore` — use `git add -f AGENTS.md` to track
- `build_notion_properties` accepts `topics` as both list and comma-separated string
- Code/Reflection truncated to 2000 chars before writing to Notion
- Notion save has retry logic: 3 attempts with backoff on rate-limit errors only
- No tests, no linter/formatter config — `pip install -r requirements.txt`
- Deploy: Railway auto-deploys from GitHub on push

## Env Vars

| Var | Required | Notes |
|---|---|---|
| `DISCORD_PUBLIC_KEY` | Yes | Discord Developer Portal → General Information |
| `DISCORD_APP_ID` | Yes | Discord numeric ID |
| `NOTION_TOKEN` | Yes | Notion Internal Integration (starts with `secret_`) |
| `NOTION_DATABASE_ID` | Yes | UUID from Notion DB URL |
| `OPENAI_API_KEY` | No* | Required only for screenshot/AI vision |
| `DISCORD_BOT_TOKEN` | No* | Required only for auto-DM weekly digest |
| `DIGEST_USER_ID` | No* | Your Discord user ID for weekly digest DM |

## Commands

```
pip install -r requirements.txt
python3 function_app.py          # run locally with uvicorn
git push origin main             # deploy to Railway (auto-deploys)
```

## Notion DB Properties

Name (title), Date (date), Problem (rich_text), Difficulty (select), Topics (multi_select), LeetCode URL (url), Code Snippet (rich_text), Reflection (rich_text).

## Register Slash Command

```
curl -X PUT \
  -H "Authorization: Bot <BOT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '[{"name":"journal","description":"Start a LeetCode journal entry or upload a screenshot","options":[{"type":11,"name":"screenshot","description":"Screenshot of your solution (optional)","required":false}]}]' \
  https://discord.com/api/v10/applications/<APP_ID>/commands
```
