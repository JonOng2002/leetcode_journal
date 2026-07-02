# LeetCode Journal — Development Plan

## Completed

- **Phase 0** — `SessionStore` falls back to in-memory dict when Table Storage is unavailable (Azurite no longer required locally).
- **Phase 1a+b** — AI vision pipeline: optional `screenshot` attachment on `/journal`, downloads image, sends to GPT-4o mini, extracts all fields, follow-up confirm/edit/cancel flow in Discord.
- **Phase 1c** — Added `OPENAI_API_KEY` to example config files.
- **Phase 2** — `GET /dashboard` endpoint with Plotly Sankey diagram showing entries grouped by SQL vs DSA with topic breakdown.

## Future

### Phase 3 — Web Frontend
Replace (or supplement) the Discord modal flow with a self-hosted web UI:
- Form for manual entry
- Dashboard with the Sankey chart
- Could live in the existing Azure Functions app (FastAPI serves HTML) or migrate to a dedicated host
- Evaluate cost: Functions Consumption (~$0/mo) vs App Service B1 (~$13/mo) when ready to consolidate with jonongca.com

### Phase 4 — LLM Follow-up & Enhancements
- AI-generated reflection refinement with dynamic follow-up questions
- Save follow-up Q&A back into Notion
- Duplicate detection across entries

### Phase 5 — LeetCode Submission Polling
- Auto-detect accepted submissions and prompt for a journal entry
- Requires LeetCode API or browser extension

### Phase 6 — GitHub Repo Sync
- After confirmation, push solution code to a private GitHub repo
