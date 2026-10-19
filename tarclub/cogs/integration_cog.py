"""
TARVeri Cross-Bot Integration Cog for TARClub.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from tarclub.config import Settings
from tarclub.database import Database
from tarclub.embeds import add_line_fields
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

    @app_commands.command(
        name="link",
        description="Link TARClub to TARVeri using a token from TARVeri /society_bridge pair.",
    )
    @app_commands.describe(token="Pairing token provided by TARVeri administrator")
    @app_commands.default_permissions(administrator=True)
    async def link(self, interaction: discord.Interaction, token: str) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        import hashlib

        valid, message = IntegrationService.validate_pairing_token(
            token, interaction.guild.id, self.settings.shared_secret
        )
        if not valid:
            await interaction.followup.send(
                f"❌ **Pairing Token Rejected:** {message}",
                ephemeral=True,
            )
            return

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        await self.db.set_integration(
            guild_id=interaction.guild_id,
            is_linked=True,
            enforce_student=True,
            shared_secret_hash=token_hash,
        )

        embed = discord.Embed(
            title="🔗 TARVeri Bridge Successfully Linked",
            description=(
                f"Society server **{interaction.guild.name}** is now paired with TARVeri!\n\n"
                "• **Cross-Bot Gating:** Committee appointments enforce student status.\n"
                "• **Auto-Audit:** Run `/integrate-tarveri audit` anytime to review health."
            ),
            color=0x00E676,
        )
        embed.set_footer(text="TARVeri & TARClub Cross-Bot Integration • TARBot-OSS")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(
        name="audit",
        description="Audit all active committee members against TARVeri verification status.",
    )
    @app_commands.default_permissions(administrator=True)
    async def audit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=False)
        society = await self.db.get_society(interaction.guild_id)
        if not society:
            await interaction.followup.send(
                "❌ This society is not registered yet. Run `/society register` first.",
                ephemeral=True,
            )
            return

        roster = await self.db.get_active_roster(society["id"])
        if not roster:
            await interaction.followup.send(
                "ℹ️ No active committee positions found to audit.",
                ephemeral=True,
            )
            return

        member_ids = [
            item["user_id"]
            for item in roster
            if item.get("user_id") and interaction.guild.get_member(item["user_id"]) is not None
        ]
        tarveri_rows = await asyncio.to_thread(
            IntegrationService.query_co_located_tarveri_students,
            self.settings.tarveri_db_path,
            member_ids,
        )

        total_positions = len(roster)
        filled_count = 0
        verified_count = 0
        unverified_count = 0
        missing_count = 0

        breakdown_lines: list[str] = []

        for item in roster:
            user_id = item.get("user_id")
            title = item.get("title", "Position")
            if not user_id:
                breakdown_lines.append(f"⚪ **{title}**: *Vacant*")
                continue

            filled_count += 1
            member = interaction.guild.get_member(user_id)
            if member is None:
                missing_count += 1
                breakdown_lines.append(
                    f"⚠️ **{title}**: <@{user_id}> (*Member not found in Discord server*)"
                )
                continue

            is_verified, role_or_fac = IntegrationService.verify_member_student_status(
                member, tarveri_rows
            )
            dept = item.get("department", "")
            if is_verified:
                verified_count += 1
                breakdown_lines.append(f"🟢 **{title}**: {member.mention} — `{role_or_fac}`")
            else:
                if dept == "MEMBERSHIP":
                    breakdown_lines.append(
                        f"⚪ **{title}**: {member.mention} — *Normal Member (Non-Verified)*"
                    )
                else:
                    unverified_count += 1
                    breakdown_lines.append(
                        f"🔴 **{title}**: {member.mention} — **Unverified Executive**"
                    )

        status_color = 0x00E676 if unverified_count == 0 and missing_count == 0 else 0xFF9800
        warn_count = unverified_count + missing_count
        embed = discord.Embed(
            title=f"📋 TARVeri Student Verification Audit — {society['name']}",
            description=(
                f"**Total Positions:** {total_positions} | **Filled:** {filled_count}\n"
                f"**Verified Students:** 🟢 {verified_count} | **Warnings:** 🔴 {warn_count}"
            ),
            color=status_color,
        )

        complete = add_line_fields(embed, "Roster Verification Status", breakdown_lines)
        footer = "TARVeri & TARClub Cross-Bot Integration • TARBot-OSS"
        if not complete:
            footer += " • Roster truncated to fit Discord embed limits"
        embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed)

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
