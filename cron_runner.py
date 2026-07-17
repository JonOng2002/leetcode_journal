#!/usr/bin/env python3
"""Standalone cron entrypoint for Railway — runs weekly digest and exits."""

import asyncio
import os
import sys

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
    _log("cron", "started")
    user_id = os.getenv("DIGEST_USER_ID", "")
    bot_token = os.getenv("DISCORD_BOT_TOKEN", "")

    if not user_id or not bot_token:
        _log("cron", "DIGEST_USER_ID or DISCORD_BOT_TOKEN not set, exiting")
        print("ERROR: Set DIGEST_USER_ID and DISCORD_BOT_TOKEN in Railway variables")
        sys.exit(1)

    digest = build_weekly_digest()
    msg = format_digest_message(digest)
    ok = await send_discord_dm(user_id, msg)
    _log("cron", f"DM sent: {ok}")


if __name__ == "__main__":
    asyncio.run(main())
