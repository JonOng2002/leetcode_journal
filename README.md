# LeetCode Journal Bot

A Discord journaling bot that captures LeetCode solve reflections and stores them in Notion. Built with **Azure Functions** (serverless — think AWS Lambda) and **FastAPI**.

When you run `/journal` in Discord, the bot walks you through a step-by-step modal chain (Problem → Difficulty → Topics → LeetCode URL → Code → Reflection) and saves the entry directly to your Notion database.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Notion Setup](#notion-setup)
- [Discord Setup](#discord-setup)
- [Local Development & Testing](#local-development--testing)
- [Deploy to Azure (Production)](#deploy-to-azure-production)
- [Bringing It All Together](#bringing-it-all-together)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)

---

## How It Works

```
Discord (/journal)  ──HTTP──►  Azure Functions  ──API──►  Notion Database
                                    ▲
                              (FastAPI handler)
```

1. A user runs `/journal` in your Discord server.
2. Discord sends an **Interaction** to your Azure Functions endpoint (`/interactions`).
3. The bot opens a series of modals, collecting one field at a time.
4. On the final step, it writes the entry to your Notion database via the Notion API.
5. Discord shows an ephemeral confirmation (only you can see it).

---

## Prerequisites

| Tool | Purpose | Install Guide |
|---|---|---|
| **Python 3.11+** | Runtime | [python.org](https://www.python.org/) |
| **Azure CLI** | Deploy & manage Azure resources | `brew install azure-cli` (macOS) or [docs](https://docs.microsoft.com/cli/azure/install-azure-cli) |
| **Azure Functions Core Tools** | Local Functions runtime + deploy | `brew install azure-functions-core-tools@4` (macOS) or [docs](https://docs.microsoft.com/azure/azure-functions/functions-run-local) |
| **ngrok** | Expose localhost during development | `brew install ngrok` (macOS) or [ngrok.com](https://ngrok.com/) |
| **Azurite** | Local Azure Storage emulator (for dev) | `brew install azurite` (macOS) or `npm install -g azurite` |

---

## Notion Setup

### 1. Create a Database

Create a new Notion database (inline or full-page) with these properties:

| Property | Type |
|---|---|
| Name | Title |
| Date | Date |
| Problem | Rich Text |
| Difficulty | Select |
| Topics | Multi-select |
| LeetCode URL | URL |
| Code Snippet | Rich Text |
| Reflection | Rich Text |

> **Tip:** You can import [assets/notion_db_template.csv](assets/notion_db_template.csv) for the structure.

### 2. Create an Internal Integration

1. Go to **[Notion Integrations](https://www.notion.so/my-integrations)**.
2. Click **New integration** → give it a name (e.g. "LeetCode Journal Bot").
3. Select the workspace where your database lives.
4. **Copy the Internal Integration Token** — it starts with `secret_`.
5. Click **Submit**.

### 3. Connect the Integration to Your Database

1. Open your Notion database page.
2. Click **⋯** (top-right) → **Connections** → **Add connections**.
3. Find and select your integration.

### 4. Get Your Database ID

The database ID is the part of the URL between the workspace name and the `?v=` parameter:

```
https://www.notion.so/<workspace>/<DATABASE_ID>?v=...
```

Copy this — you'll need it for configuration.

---

## Discord Setup

### 1. Create a Discord Application

1. Go to the **[Discord Developer Portal](https://discord.com/developers/applications)**.
2. Click **New Application** → name it (e.g. "LeetCode Journal").
3. Go to the **Bot** tab → **Reset Token** → copy the bot token.
4. Under **Privileged Gateway Intents**, enable **Message Content Intent** if needed.
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Use Slash Commands`
   - Use the generated URL to invite the bot to your server.

### 2. Copy Your Credentials

From the **General Information** tab, copy:
- **Application ID** (the numeric ID)
- **Public Key** (the hex string)

You'll need these for configuration.

### 3. Register the `/journal` Slash Command

Run this once to register the command with Discord:

```bash
curl -X PUT \
  -H "Authorization: Bot <YOUR_BOT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '[{"name":"journal","description":"Start a LeetCode journal entry"}]' \
  https://discord.com/api/v10/applications/<YOUR_APP_ID>/commands
```

Replace `<YOUR_BOT_TOKEN>` and `<YOUR_APP_ID>` with the values from above. You only need to do this once — the command persists until you change or delete it.

---

## Local Development & Testing

Test the bot on your machine before deploying to Azure.

### 1. Set Up Environment Variables

Copy the example config and fill in your values:

```bash
cp config/local.settings.example.json local.settings.json
```

Or create a `.env` file:

```env
DISCORD_PUBLIC_KEY=your_discord_public_key
DISCORD_APP_ID=your_discord_application_id
NOTION_TOKEN=secret_your_notion_integration_token
NOTION_DATABASE_ID=your_notion_database_id
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the Local Stack

In **Terminal 1** — Start the storage emulator:

```bash
azurite
```

In **Terminal 2** — Start the Functions host:

```bash
func start
```

You should see output like:
```
Functions:
        AsgiFunctionApp: http://localhost:7071
```

### 4. Expose via ngrok

In **Terminal 3**:

```bash
ngrok http 7071
```

Copy the HTTPS URL (e.g. `https://abc123.ngrok-free.dev`).

### 5. Point Discord to Your Local Instance

1. Go to your Discord app in the **[Developer Portal](https://discord.com/developers/applications)**.
2. **General Information** → set **Interactions Endpoint URL** to:
   ```
   https://<your-ngrok-url>.ngrok-free.dev/interactions
   ```
3. Click **Save** — Discord will POST a verification request. If it succeeds, you're ready.

### 6. Test

Go to your Discord server and type `/journal`. The first modal should appear.

---

## Deploy to Azure (Production)

### 1. Log In to Azure

```bash
az login
```

### 2. Create a Resource Group and Function App

```bash
# Create a resource group (choose a region near you)
az group create --name leetcode-journal-rg --location eastus

# Create a storage account (required by Azure Functions)
az storage account create --name lcjournalsa --location eastus --resource-group leetcode-journal-rg --sku Standard_LRS

# Create the Function App (Python 3.11, Consumption plan)
az functionapp create \
  --name ljfuncapp \
  --resource-group leetcode-journal-rg \
  --consumption-plan-location eastus \
  --os-type Linux \
  --runtime python \
  --runtime-version 3.11 \
  --functions-version 4 \
  --storage-account lcjournalsa
```

> **Note:** The `--name` must be globally unique. Replace `ljfuncapp` with something available.

### 3. Set Environment Variables in Azure

```bash
az functionapp config appsettings set \
  --name ljfuncapp \
  --resource-group leetcode-journal-rg \
  --settings \
    DISCORD_PUBLIC_KEY="your_discord_public_key" \
    DISCORD_APP_ID="your_discord_application_id" \
    NOTION_TOKEN="secret_your_notion_integration_token" \
    NOTION_DATABASE_ID="your_notion_database_id"
```

### 4. Deploy Your Code

```bash
func azure functionapp publish ljfuncapp
```

### 5. Point Discord to Your Azure URL

1. In the **[Discord Developer Portal](https://discord.com/developers/applications)**, set the **Interactions Endpoint URL** to:
   ```
   https://ljfuncapp.azurewebsites.net/interactions
   ```
   (Replace `ljfuncapp` with your function app name.)
2. Discord will verify the endpoint. If it succeeds, you're live!

### 6. Future Deployments

After making code changes, just run:

```bash
func azure functionapp publish ljfuncapp
```

---

## Bringing It All Together

Here's a checklist for the full end-to-end setup:

| Step | Done? |
|---|---|
| Create a Notion database with the required properties | [ ] |
| Create a Notion internal integration and get the `secret_` token | [ ] |
| Share your database with the integration | [ ] |
| Create a Discord application and invite the bot to a server | [ ] |
| Copy Discord Application ID and Public Key | [ ] |
| Register the `/journal` slash command via curl | [ ] |
| Deploy the bot to Azure Functions | [ ] |
| Set environment variables in Azure App Settings | [ ] |
| Set Discord Interactions URL to `https://<your-app>.azurewebsites.net/interactions` | [ ] |
| Run `/journal` in Discord | [ ] |

---

## Troubleshooting

### "Interactions Endpoint could not be verified"

- **Cause:** Discord couldn't reach your endpoint or signature verification failed.
- **Check:** Is the function app running? (`func start` locally, or deployed to Azure?)
- **Check:** Is `DISCORD_PUBLIC_KEY` correct in your environment variables?
- **Check:** Is the URL exactly `https://<host>/interactions` (no trailing slash)?

### "Notion error: API token is invalid"

- **Cause:** The `NOTION_TOKEN` is wrong, expired, or the integration isn't connected to the database.
- **Fix:** Use an **Internal Integration** token (starts with `secret_`), not an OAuth `ntn_` token.
- **Fix:** Ensure the integration is added under the database's **Connections** menu.

### "Notion error: 404 - Notion API"

- **Cause:** The `NOTION_DATABASE_ID` is wrong.
- **Check:** The database ID in your URL — it's the UUID after the workspace name. Make sure you didn't drop a character (e.g., leading `3`).

### Bot doesn't respond to `/journal`

- **Cause:** The slash command wasn't registered, or the bot isn't in the server.
- **Fix:** Run the `curl` command from [Register the `/journal` Command](#3-register-the-journal-slash-command).
- **Fix:** Make sure the bot has `applications.commands` scope when invited.

### "400" or "Invalid URL" when saving Discord Interactions URL

- **Cause:** The URL is missing `/interactions` or has a trailing slash.
- **Fix:** Use exactly `https://<host>/interactions` — no trailing `/`.

---

## Project Structure

```text
leetcode-journal-bot/
├── function_app.py              # Azure Functions (FastAPI) entrypoint — all logic lives here
├── host.json                    # Functions host configuration
├── requirements.txt             # Python dependencies
├── local.settings.json          # Local env vars (gitignored)
├── .env                         # Alternative env file (gitignored)
├── config/
│   ├── local.settings.example.json
│   └── .env.example
├── assets/
│   └── notion_db_template.csv   # Notion database structure for import
├── agents.md                    # AI agent instructions for development
└── README.md                    # You are here
```

---

## Journal Steps

The bot collects one field at a time via a modal chain:

| # | Field | Type | Example |
|---|---|---|---|
| 1 | **Problem** | Short text | `Two Sum` |
| 2 | **Difficulty** | Dropdown | `Easy` / `Medium` / `Hard` |
| 3 | **Topics** | Short text | `Array, Hash Map` |
| 4 | **LeetCode URL** | Short text | `https://leetcode.com/problems/two-sum/` |
| 5 | **Code** | Paragraph | Your solution |
| 6 | **Reflection** | Paragraph | Key insight, struggles, takeaways |

---

## Configuration Reference

All configuration is done via environment variables:

| Variable | Description | Required |
|---|---|---|
| `DISCORD_PUBLIC_KEY` | From Discord Developer Portal → General Information | Yes |
| `DISCORD_APP_ID` | From Discord Developer Portal → General Information (numeric ID) | Yes |
| `NOTION_TOKEN` | From Notion Integrations — Internal Integration token (starts with `secret_`) | Yes |
| `NOTION_DATABASE_ID` | UUID from your Notion database URL | Yes |

For local dev, set these in `local.settings.json` under `Values`:

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "DISCORD_PUBLIC_KEY": "...",
    "DISCORD_APP_ID": "...",
    "NOTION_TOKEN": "...",
    "NOTION_DATABASE_ID": "..."
  }
}
```

For production, set them via `az functionapp config appsettings set` (see [Deploy to Azure](#deploy-to-azure-production)).

---

## Roadmap

### v1 (Current)

- `/journal` slash command with step-by-step modal chain
- Save to Notion with retry logic
- Discord Interactions signature verification
- Ephemeral confirmations

### v2

- LLM-generated summaries and dynamic follow-up questions
- Save follow-up Q&A back into Notion
- GitHub private repo sync after confirmation
- Duplicate detection

### v3

- LeetCode submission polling (accepted submissions) — automatically detect when you solve a problem and prompt for a journal entry
