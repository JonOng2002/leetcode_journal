# LeetCode Journal — Agent Guide

Single-file Azure Functions Python v2 app (`AsgiFunctionApp` + FastAPI) acting as a Discord bot. Collects LeetCode reflections via Discord modals OR screenshot → AI vision → Notion.

## Key Files

| File | Purpose |
|---|---|
| `function_app.py` | Entire app in one file (Python v2, no `function.json`) |
| `PLANS.md` | Future features roadmap |
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

- **With screenshot**: Downloads image → GPT-4o mini → extracts fields → shows preview with Confirm / Edit Topics / Cancel buttons. Runs in a background thread (`threading.Thread` with `daemon=True`). Initial response is a loading message, then the background thread patches the Discord webhook with results.
- **Without screenshot**: Existing 6-step modal chain (Problem → Difficulty dropdown → Topics → URL → Code → Reflection → Save).

## Session Store (`SessionStore`)

- Backed by Azure Table Storage (`leetcodejournalsessions` table, partition key `default`).
- **Falls back to in-memory dict** if Table Storage is unavailable (no Azurite needed for local dev).
- 15-minute TTL. Expired sessions silently deleted on access.

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

## AI Vision

- Provider: OpenAI GPT-4o mini (configurable via `OPENAI_API_KEY`)
- Prompt extracts: `problem`, `difficulty`, `topics`, `leetcode_url`, `code`, `is_sql`, `reflection`
- Topics classified into fixed NeetCode DSA tags list (see `NEETCODE_TOPICS` in code)
- SQL problems detected by `is_sql` flag
- Preview shows all extracted fields with Confirm/Edit Topics/Cancel buttons
- Edit Topics opens a modal pre-filled with current tags

## Dashboard

`GET /dashboard` — queries all Notion DB entries → aggregates by SQL vs DSA topics → renders interactive Plotly Sankey diagram as HTML.

## Architecture Notes

- `host.json` sets `"routePrefix": ""` — endpoint is `/interactions`, not `/api/interactions`
- `agent.md` is in `.gitignore` and `.funcignore` — use `git add -f AGENTS.md` to track
- `build_notion_properties` accepts `topics` as both list and comma-separated string
- Code/Reflection truncated to 2000 chars before writing to Notion
- Notion save has retry logic: 3 attempts with backoff on rate-limit errors only
- No tests, no linter/formatter config — `pip install -r requirements.txt`
- Deploy: `func azure functionapp publish ljfuncapp`

## Env Vars

| Var | Required | Notes |
|---|---|---|
| `DISCORD_PUBLIC_KEY` | Yes | Discord Developer Portal → General Information |
| `DISCORD_APP_ID` | Yes | Discord numeric ID |
| `NOTION_TOKEN` | Yes | Notion Internal Integration (starts with `secret_`) |
| `NOTION_DATABASE_ID` | Yes | UUID from Notion DB URL |
| `OPENAI_API_KEY` | No* | Required only for screenshot/AI vision |
| `AZURE_STORAGE_CONNECTION_STRING` | No | Defaults to `UseDevelopmentStorage=true` |
| `NOTION_DATABASE_URL` | No | Adds "View all entries" link after save |

## Commands

```
pip install -r requirements.txt
azurite                     # Terminal 1 (optional — in-memory fallback works)
func start                  # Terminal 2 (port 7071)
ngrok http 7071             # Terminal 3
func azure functionapp publish ljfuncapp   # deploy to Azure
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
