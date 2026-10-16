"""
TARClub Discord Bot lifecycle manager.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from tarclub.config import Settings
from tarclub.database import Database

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

    async def close(self) -> None:
        """Gracefully close database and bot session."""
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

    async with bot:
        await bot.start(settings.discord_token)
