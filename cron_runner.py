#!/usr/bin/env python3
"""Standalone cron entrypoint for Railway — runs weekly digest and exits."""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from function_app import (
    build_weekly_digest,
    format_digest_message,
    send_discord_dm,
    _log,
)

load_dotenv()


async def main():
    print("=== cron_runner started ===", flush=True)
    utc_now = datetime.now(timezone.utc)
    sgt_now = utc_now + timedelta(hours=8)
    _log("cron", f"started at UTC={utc_now.strftime('%H:%M')} SGT={sgt_now.strftime('%H:%M')}")
    user_id = os.getenv("DIGEST_USER_ID", "")
    bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
    print(f"DIGEST_USER_ID: {'set' if user_id else 'MISSING'}", flush=True)
    print(f"DISCORD_BOT_TOKEN: {'set' if bot_token else 'MISSING'}", flush=True)

    if not user_id or not bot_token:
        _log("cron", "DIGEST_USER_ID or DISCORD_BOT_TOKEN not set, exiting")
        print("ERROR: Set DIGEST_USER_ID and DISCORD_BOT_TOKEN in Railway variables", flush=True)
        sys.exit(1)

    print("Building digest...", flush=True)
    digest = build_weekly_digest()
    print(f"Digest built: {digest['total']} entries found", flush=True)
    msg = format_digest_message(digest)
    print("Sending DM...", flush=True)
    ok = await send_discord_dm(user_id, msg)
    print(f"DM sent: {ok}", flush=True)
    _log("cron", f"DM sent: {ok}")


if __name__ == "__main__":
    asyncio.run(main())
