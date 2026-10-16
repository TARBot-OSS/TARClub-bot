"""
TARVeri Cross-Bot Integration Cog for TARClub.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from tarclub.config import Settings
from tarclub.database import Database
from tarclub.services.integration_service import IntegrationService

logger = logging.getLogger("tarclub.cogs.integration")


class IntegrationCog(commands.GroupCog, group_name="integrate-tarveri"):
    """Commands for managing TARVeri cross-bot integration and verification gating."""

    def __init__(self, bot: commands.Bot, db: Database, settings: Settings) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        super().__init__()

    @app_commands.command(
        name="status",
        description="Check status of the TARVeri verification bridge in this server.",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=False)
        tarveri_present = IntegrationService.is_tarveri_present(
            interaction.guild, self.settings.tarveri_bot_id
        )
        integration = await self.db.get_integration(interaction.guild_id)

        is_linked = bool(integration and integration.get("is_linked")) or tarveri_present
        enforce = bool(integration.get("enforce_student_verification", 1)) if integration else True

        embed = discord.Embed(
            title="🔗 TARVeri Cross-Bot Integration Status",
            color=0x00E676 if is_linked else 0xFF9800,
        )
        embed.add_field(
            name="🤖 TARVeri Bot Detection",
            value="🟢 **Detected in Server**" if tarveri_present else "⚪ *Not in this server*",
            inline=True,
        )
        embed.add_field(
            name="🤝 Bridge State",
            value="🟢 **ACTIVE & PAIRED**" if is_linked else "⚪ *Unlinked*",
            inline=True,
        )
        embed.add_field(
            name="🛡️ Committee Student Gating",
            value="🔒 **Enforced** (Verified Students Only)" if enforce else "🔓 *Disabled (Open)*",
            inline=False,
        )

        embed.set_footer(text="TARVeri & TARClub Cross-Bot Integration • TARBot-OSS")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="toggle-enforce",
        description="Toggle whether committee appointments require TARVeri student verification.",
    )
    @app_commands.describe(enabled="True to require student verification, False to disable")
    @app_commands.default_permissions(administrator=True)
    async def toggle_enforce(self, interaction: discord.Interaction, enabled: bool) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        await self.db.set_integration(
            guild_id=interaction.guild_id,
            is_linked=True,
            enforce_student=enabled,
        )

        status_text = "ENFORCED (Verified Students Only)" if enabled else "DISABLED (Open to all)"
        await interaction.followup.send(
            f"✅ Committee student verification requirement is now **{status_text}**.",
            ephemeral=False,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Auto-detect TARVeri bot joining the server and activate the bridge."""
        if member.id == self.settings.tarveri_bot_id:
            logger.info(
                "TARVeri bot joined guild %s (%s). Activating bridge.",
                member.guild.name,
                member.guild.id,
            )
            await self.db.set_integration(
                guild_id=member.guild.id,
                is_linked=True,
                enforce_student=True,
            )


async def setup(bot: commands.Bot) -> None:
    db = getattr(bot, "db", None)
    settings = getattr(bot, "settings", None)
    if db is None or settings is None:
        raise RuntimeError("Bot missing db or settings.")
    await bot.add_cog(IntegrationCog(bot, db, settings))
