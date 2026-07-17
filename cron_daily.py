#!/usr/bin/env python3
"""Standalone cron entrypoint for Railway — daily reminder DM."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

# Must import after path setup
from function_app import (
    _today_count,
    send_discord_dm,
    _log,
)
import asyncio


async def main():
    print("=== cron_daily started ===", flush=True)
    user_id = os.getenv("DIGEST_USER_ID", "")
    bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
    print(f"DIGEST_USER_ID: {'set' if user_id else 'MISSING'}", flush=True)
    print(f"DISCORD_BOT_TOKEN: {'set' if bot_token else 'MISSING'}", flush=True)

    if not user_id or not bot_token:
        print("ERROR: Set DIGEST_USER_ID and DISCORD_BOT_TOKEN", flush=True)
        sys.exit(1)

    count = _today_count()
    print(f"Entries today: {count}", flush=True)

    if count > 0:
        msg = (
            f"✅ You already journaled **{count}** time{'s' if count > 1 else ''} today. "
            "Great work! 🔥"
        )
    else:
        msg = (
            "📝 **No journal entry yet today!**\n\n"
            "Run `/journal` or upload a screenshot of your LeetCode solution. "
            "Keep the streak alive! 💪"
        )

    ok = await send_discord_dm(user_id, msg)
    print(f"DM sent: {ok}", flush=True)
    _log("cron", f"daily DM sent: {ok}")


if __name__ == "__main__":
    asyncio.run(main())
