"""
TARClub entry point for:
    python -m tarclub
"""

from __future__ import annotations

import asyncio
import logging
import sys

from tarclub.bot import run_bot
from tarclub.config import Settings

logger = logging.getLogger("tarclub")


def main() -> None:
    try:
        settings = Settings.from_env()
    except RuntimeError as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        asyncio.run(run_bot(settings))
    except (KeyboardInterrupt, SystemExit):
        logger.info("TARClub process terminated cleanly.")


if __name__ == "__main__":
    main()
