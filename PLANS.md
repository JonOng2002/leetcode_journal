# LeetCode Journal — Development Plan

## Completed

- **Phase 0** — SessionStore in-memory fallback (no Azurite needed).
- **Phase 1a+b** — AI vision pipeline: screenshot → GPT-4o mini → extract fields → confirm/edit/cancel.
- **Phase 1c** — Added `OPENAI_API_KEY` to example config files.
- **Phase 2** — `GET /dashboard` Sankey diagram with 3-layer breakdown (category → difficulty → topic).
- **Phase 2b** — Polish Reflection flow: modal for rough thoughts, AI rewrites into polished reflection.
- **Phase 2c** — `GET /debug/logs` cross-instance logging.
- **Phase 2d** — `GET /keepalive` endpoint (for warmup, no longer needed after Railway).
- **Phase 2e** — `/ping` Discord command.
- **Phase 3a** — Migrated from Azure Functions → Railway (no cold starts, no Azure deps).
- **Phase 3b** — Created AGENTS.md + PLANS.md for future agents.

## Future

### Priority — Motivation & Habit Building
- **Weekly digest DM** — bot sends a Sunday summary: problems solved, streak, topic breakdown.
- **Streak counter** — on every save: "Day 12 streak! 🔥"
- **Gap analysis** — "You haven't touched Graphs or DP yet. Try one today?"

### Phase 4 — Web Frontend
- Replace (or supplement) Discord with a browser UI.
- Custom domain on Railway (`journal.jonongca.com`).
- Dashboard with Sankey + activity heatmap + stats.

### Phase 5 — GitHub Repo Sync
- After confirmation, push solution code to a private GitHub repo.
- Private repo commits appear in contribution heatmap.

### Phase 6 — LeetCode Submission Polling
- Auto-detect accepted submissions and prompt for a journal entry.

### Phase 7 — Custom Domain
- Point a subdomain to Railway for a cleaner dashboard URL.
