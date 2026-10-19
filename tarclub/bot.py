"""
TARClub Discord Bot lifecycle manager.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import discord
from discord.ext import commands

from tarclub.config import Settings
from tarclub.database import Database
from tarclub.services.console_service import ConsoleService

logger = logging.getLogger("tarclub")


class TARClubBot(commands.Bot):
    """Core TARClub bot instance."""

    def __init__(self, settings: Settings, db: Database) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned_or("!tc "),
            intents=intents,
            application_id=settings.application_id,
        )
        self.settings = settings
        self.db = db
        self.console_service = ConsoleService(self, self.db, self.settings)
        self.console_service.attach_log_capture()

    async def setup_hook(self) -> None:
        """Initialize database, register persistent views, and load cogs."""
        await self.db.connect()
        await self.db.init_tables()

        # Load extension cogs
        await self.load_extension("tarclub.cogs.society_cog")
        await self.load_extension("tarclub.cogs.committee_cog")
        await self.load_extension("tarclub.cogs.term_cog")
        await self.load_extension("tarclub.cogs.integration_cog")

        # Sync slash commands
        logger.info("Syncing application command tree...")
        await self.tree.sync()
        logger.info("Application command tree synced successfully.")

    async def on_ready(self) -> None:
        logger.info(
            "Logged in as %s (ID: %s) | Connected to %d guilds",
            self.user,
            self.user.id if self.user else "Unknown",
            len(self.guilds),
        )
        if self.console_service:
            self.console_service.start()

    async def close(self) -> None:
        """Gracefully close database and bot session."""
        if getattr(self, "console_service", None):
            self.console_service.stop()
        await self.db.close()
        await super().close()
        logger.info("TARClub process shut down cleanly.")


async def run_bot(settings: Settings) -> None:
    """Entry point coroutine for starting the bot."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s : %(message)s",
    )
    db = Database(settings.database_path)
    bot = TARClubBot(settings, db)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_signal(sig: int) -> None:
        if not stop_event.is_set():
            stop_event.set()
            try:
                sig_name = signal.Signals(sig).name
            except (ValueError, AttributeError):
                sig_name = str(sig)
            logger.info("Signal %s received. Closing TARClub gracefully...", sig_name)

            async def _graceful_close() -> None:
                try:
                    await asyncio.wait_for(bot.close(), timeout=5.0)
                except Exception as e:
                    logger.warning("Error during TARClub shutdown: %s", e)
                finally:
                    loop.call_soon(sys.exit, 0)

            asyncio.create_task(_graceful_close())
        else:
            logger.warning("Repeated signal received. Forcing immediate termination.")
            os._exit(1)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))
        except (NotImplementedError, RuntimeError):
            pass

    async with bot:
        await bot.start(settings.discord_token)
