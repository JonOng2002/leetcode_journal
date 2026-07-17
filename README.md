# LeetCode Journal Bot

A **personal learning tracker** that turns LeetCode problem-solving into a measurable habit. Snap a screenshot of your solution → AI extracts everything → saves to Notion. No manual forms.

---

## Architecture at a Glance

```
Discord                    FastAPI (Railway)                  Notion
  │                             │                              │
  ├─ /journal (screenshot) ───► ├─ GPT-4o mini vision ───────► │
  │                             │    extracts fields           │
  ├─ /journal (modal) ────────► ├─ 6-step modal chain ──────► │
  │                             │                              │
  ├─ /ping ───────────────────► ├─ health check               │
  │                             │                              │
  ├─ /digest ─────────────────► ├─ queries DB ──────────────► │
  │                             │    AI summary + DM           │
  │                             │                              │
  │                     ┌───────┤                              │
  │                     │ Cron  ├─ Weekly digest (Sun noon)    │
  │                     │       ├─ Daily reminder (8 PM)       │
  │                     └───────┘                              │
  │                             │                              │
  Web Browser                  │                              │
  ├─ /dashboard ──────────────►├─ Plotly Sankey (live data)   │
  ├─ /debug/logs ─────────────►├─ cross-instance logs         │
  └─ /keepalive ──────────────►└─ warmup endpoint             │
```

**Hosting:** Railway (Hobby, $5/mo) — single container, always warm, no cold starts.
**Database:** Notion API (your existing workspace) — no separate DB to manage.

---

## Key Design Decisions

### Why AI Vision over Manual Entry?

The original 6-step Discord modal worked, but it was tedious. Every problem solved required typing: problem name, difficulty, topics, URL, code, and reflection — easily 2-3 minutes per entry.

**Solution:** GPT-4o mini vision. One screenshot → AI extracts problem name, difficulty, code, topics, and generates a reflection. You just review and confirm in one click.

The AI also classifies each problem as **SQL** or **DSA** (exactly one must be true), and tags it with accurate NeetCode roadmap topics. SQL problems get sub-tags like *Joins, Aggregation, Subqueries, CTEs, Window Functions, Basic Queries*.

### Why Notion Instead of PostgreSQL/MongoDB?

The goal was zero infrastructure. No database provisioning, no migrations, no backup scripts. Notion is already my workspace — the bot writes directly to a shared database. Every entry is immediately searchable, editable, and shareable without building a UI.

### Why Railway over Azure Functions?

Started on Azure Functions Consumption plan (~$0/mo). Cold starts were 5-10 seconds, which exceeded Discord's 3-second interaction timeout. Moved to Railway for consistent sub-second response times and simpler deployment (auto-deploys from GitHub, no Dockerfile needed).

### Why a Single 900+ Line File?

The entire application lives in one file (`function_app.py`). For a single-developer personal tool with zero external contributors, this is deliberate:

- **No package boundaries to navigate** — every function is one grep away
- **Instant understanding** — open one file, see the full request lifecycle
- **Zero import wiring** — no circular dependency surprises

If this ever grows beyond a solo project, the extraction points are obvious:
- Session store → separate module
- Discord interaction handlers → router
- Dashboard → separate service

---

## Features

### Core Journaling

| Feature | Description |
|---|---|
| **Screenshot → AI** | Drop a screenshot of your LeetCode solution; GPT-4o mini extracts everything |
| **Manual modal** | 6-step form (Problem → Difficulty → Topics → URL → Code → Reflection) |
| **Edit topics** | Modal to correct AI's topic classification before saving |
| **Polish reflection** | Type rough thoughts → AI rewrites into a clean, well-written reflection |
| **Notion save** | Writes directly to your Notion database with retry logic |

### Intelligence

| Feature | Description |
|---|---|
| **SQL vs DSA detection** | AI classifies each problem with strong separation (is_sql / is_dsa) |
| **NeetCode topic tagging** | All 18 DSA roadmap categories + 6 SQL sub-tags |
| **Reflection generation** | AI writes a natural reflection from the screenshot context |

### Motivation & Habit Building

| Feature | Description |
|---|---|
| **Weekly digest DM** | Every Sunday noon — stats, topic breakdown, SQL/DSA split, AI-written summary using your own reflections |
| **Daily reminder DM** | Every evening — nudges you if you haven't journaled yet |
| **Interactive dashboard** | `/dashboard` — Plotly Sankey diagram showing your learning distribution by category × difficulty × topic |

### Operations

| Feature | Description |
|---|---|
| **Health check** | `/ping` — verify the bot is alive from Discord |
| **Debug logs** | `/debug/logs` — server-side event log, survives across container instances |
| **Keepalive** | `/keepalive` — for external uptime monitors |
| **Ed25519 verification** | Every Discord request cryptographically verified |

---

## Live Dashboard

See your progress in real-time:

```
https://leetcodejournal-production.up.railway.app/dashboard
```

Interactive Sankey diagram showing:
- **Category:** SQL vs DSA
- **Difficulty:** Easy / Medium / Hard
- **Topics:** Specific tags per problem

Data is queried live from your Notion database — zero caching.

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| **Runtime** | Python 3.11 | Discord libraries, OpenAI SDK, Notion SDK all mature on Python |
| **Web framework** | FastAPI | Async-native, auto-docs, lightweight — no Django overhead for a single endpoint |
| **AI** | GPT-4o mini (OpenAI) | ~$0.15/1M input tokens — a screenshot costs < $0.001 |
| **Hosting** | Railway | Single-container simplicity, auto-deploys from GitHub, no cold starts |
| **Database** | Notion API | Zero infra, already part of my workflow |
| **Vision** | GPT-4o mini vision | Reads code, badges, layouts from screenshots accurately |
| **Session** | In-memory dict | Single-container means no distributed state problem |
| **Visualization** | Plotly | Interactive Sankey diagrams, HTML export, free |

---

## Quick Start

```bash
git clone https://github.com/JonOng2002/leetcode_journal.git
cd leetcode_journal
pip install -r requirements.txt
python3 function_app.py          # runs on localhost:8080
```

### Environment Variables

| Variable | Required | Source |
|---|---|---|
| `DISCORD_PUBLIC_KEY` | Yes | Discord Developer Portal → General Information |
| `DISCORD_APP_ID` | Yes | Discord Developer Portal → General Information |
| `NOTION_TOKEN` | Yes | Notion Integrations (starts with `secret_`) |
| `NOTION_DATABASE_ID` | Yes | UUID from Notion DB URL |
| `OPENAI_API_KEY` | For vision | OpenAI API keys |
| `DISCORD_BOT_TOKEN` | For DMs | Discord Developer Portal → Bot |
| `DIGEST_USER_ID` | For DMs | Your Discord user ID |

### Register Slash Commands

```bash
curl -X PUT \
  -H "Authorization: Bot <BOT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '[
    {"name":"journal","description":"Start a LeetCode journal entry or upload a screenshot","options":[{"type":11,"name":"screenshot","required":false}]},
    {"name":"ping","description":"Check if the bot is alive"},
    {"name":"digest","description":"Get your weekly learning summary"}
  ]' \
  https://discord.com/api/v10/applications/<APP_ID>/commands
```

---

## Project Structure

```
├── function_app.py          # Main application (FastAPI + all handlers)
├── cron_runner.py           # Weekly digest cron entrypoint (Railway cron)
├── cron_daily.py            # Daily reminder cron entrypoint (Railway cron)
├── Procfile                 # Railway start command
├── railway.json             # Railway build configuration
├── requirements.txt         # Python dependencies
├── AGENTS.md                # AI agent instructions
├── PLANS.md                 # Roadmap
├── config/
│   ├── .env.example
│   └── local.settings.example.json
└── assets/
    └── notion_db_template.csv
```

---

## What I'd Do Differently Next Time

- **Use a proper database early.** In-memory sessions work for a single container, but if I ever scale horizontally, I'd swap in Redis in minutes.
- **Async from day one.** The original threading-based vision processing caused issues with Azure Functions' execution model. Async (`asyncio.create_task`) was cleaner.
- **Add a web UI sooner.** Discord modals are limiting for rich editing. A React frontend with the same FastAPI backend would be more powerful.
---

## Roadmap

See [PLANS.md](PLANS.md) for detailed status.

- **Streak counter** — "Day 12 streak! 🔥" on every save
- **Gap analysis** — "You haven't touched Graphs yet. Try one?"
- **GitHub auto-commit** — push accepted solutions to a private repo
- **Custom domain** — `journal.jonongca.com` on Railway
- **LeetCode submission polling** — auto-detect accepted solutions
