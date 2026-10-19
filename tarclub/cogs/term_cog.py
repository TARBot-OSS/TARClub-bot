"""
Annual committee term and handover management Cog for TARClub.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from tarclub.database import Database
from tarclub.services.rbac_service import SocietyPermission, has_permission

logger = logging.getLogger("tarclub.cogs.term")


def can_confirm_handover(user_id: int, pending: dict, owner_id: int) -> bool:
    return user_id in (pending["incoming_chair_id"], owner_id)


def alumni_grant_blocker(guild: discord.Guild, academic_year: str) -> str | None:
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return "The bot needs the Manage Roles permission to grant Alumni roles."
    role = discord.utils.get(guild.roles, name=f"Alumni Exco ({academic_year})")
    if role is not None and role.position >= me.top_role.position:
        return f"Move the bot's role above `{role.name}` in Server Settings, then confirm again."
    return None


async def grant_alumni_roles(
    guild: discord.Guild, academic_year: str, member_ids: list[int]
) -> tuple[int, int]:
    """Grant the Alumni Exco role for the concluded year. Returns (granted, failed)."""
    role_name = f"Alumni Exco ({academic_year})"
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        try:
            role = await guild.create_role(name=role_name, reason="TARClub term handover")
        except discord.HTTPException:
            return 0, len(member_ids)

    granted = failed = 0
    for user_id in member_ids:
        member = guild.get_member(user_id)
        if member is None:
            failed += 1
            continue
        try:
            await member.add_roles(role, reason="TARClub term handover")
            granted += 1
        except discord.HTTPException:
            failed += 1
    return granted, failed


class TermCog(commands.GroupCog, group_name="term"):
    """Commands for managing annual committee terms and executing election handovers."""

    def __init__(self, bot: commands.Bot, db: Database) -> None:
        self.bot = bot
        self.db = db
        super().__init__()

    async def _check_permission(
        self, interaction: discord.Interaction, required_perm: SocietyPermission
    ) -> tuple[dict | None, bool]:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return None, False

        society = await self.db.get_society(interaction.guild_id)
        if not society:
            return None, False

        is_admin = (
            interaction.user.guild_permissions.administrator
            or interaction.user.id == interaction.guild.owner_id
        )
        if is_admin:
            return society, True

        user_mask = await self.db.get_user_permissions(society["id"], interaction.user.id)
        return society, has_permission(user_mask, required_perm)

    @app_commands.command(
        name="history",
        description="View all active and archived committee terms.",
    )
    async def history(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=False)
        society = await self.db.get_society(interaction.guild_id)
        if not society:
            await interaction.followup.send("❌ Society not registered.", ephemeral=True)
            return

        terms = await self.db.get_term_history(society["id"])
        color_val = 0x1E88E5
        try:
            color_val = int(society["primary_color"].lstrip("#"), 16)
        except Exception:
            pass

        embed = discord.Embed(
            title=f"📜 {society['name']} — Term History",
            description=f"Total terms on record: **{len(terms)}**\n",
            color=color_val,
        )

        for t in terms:
            status = "🟢 **ACTIVE**" if t["is_active"] else "📁 *ARCHIVED*"
            handover_info = f" • Handed over: {t['handover_at']}" if t.get("handover_at") else ""
            embed.add_field(
                name=f"{t['term_name']} ({t['academic_year']})",
                value=f"Status: {status}{handover_info}",
                inline=False,
            )

        embed.set_footer(text="TARClub Annual Term Lifecycle")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="handover",
        description="Propose the annual committee handover; a second member must confirm it.",
    )
    @app_commands.describe(
        next_term="Incoming Academic Year (e.g. 2026/2027)",
        incoming_chair="Incoming Chairperson, who must confirm the handover",
        notes="Optional handover notes, proclamation, or AGM resolution remarks",
    )
    async def handover(
        self,
        interaction: discord.Interaction,
        next_term: str,
        incoming_chair: discord.Member,
        notes: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=False)
        society, authorized = await self._check_permission(
            interaction, SocietyPermission.MANAGE_TERMS
        )
        if not society or not authorized:
            await interaction.followup.send(
                "❌ You do not have permission to execute term handovers (`MANAGE_TERMS`).",
                ephemeral=True,
            )
            return

        active_term = await self.db.get_active_term(society["id"])
        if not active_term:
            await interaction.followup.send("❌ No active term to handover from.", ephemeral=True)
            return

        if incoming_chair.id == interaction.user.id:
            await interaction.followup.send(
                "❌ The proposer cannot also be the incoming chairperson.", ephemeral=True
            )
            return

        if await self.db.get_pending_handover(society["id"]):
            await interaction.followup.send(
                "⏳ A handover is already pending. Ask a second committee member to run "
                "`/term confirm-handover`, or run `/term cancel-handover`.",
                ephemeral=True,
            )
            return

        await self.db.propose_handover(
            society_id=society["id"],
            current_term_id=active_term["id"],
            next_academic_year=next_term.strip(),
            handover_notes=notes,
            proposed_by=interaction.user.id,
            incoming_chair_id=incoming_chair.id,
        )
        await interaction.followup.send(
            f"📝 Handover to `Term {next_term.strip()}` proposed. {incoming_chair.mention} "
            "(incoming chairperson) or the server owner must run `/term confirm-handover`.",
        )

    @app_commands.command(
        name="confirm-handover",
        description="Confirm a pending handover as the second committee member.",
    )
    async def confirm_handover(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=False)
        society = await self.db.get_society(interaction.guild_id)
        if not society:
            await interaction.followup.send(
                "❌ This server is not registered as a society.", ephemeral=True
            )
            return

        pending = await self.db.get_pending_handover(society["id"])
        if not pending:
            await interaction.followup.send("❌ No handover is pending.", ephemeral=True)
            return
        if not can_confirm_handover(interaction.user.id, pending, interaction.guild.owner_id):
            await interaction.followup.send(
                "❌ Only the incoming chairperson or the server owner can confirm this handover.",
                ephemeral=True,
            )
            return
        if pending["proposed_by"] == interaction.user.id:
            await interaction.followup.send(
                "❌ The proposer cannot confirm their own handover. "
                "A second committee member must.",
                ephemeral=True,
            )
            return

        active_term = await self.db.get_active_term(society["id"])
        if not active_term or active_term["id"] != pending["current_term_id"]:
            await interaction.followup.send(
                "❌ The active term changed after this handover was proposed. "
                "Run `/term cancel-handover` and propose again.",
                ephemeral=True,
            )
            return

        blocker = alumni_grant_blocker(interaction.guild, active_term["academic_year"])
        if blocker:
            await interaction.followup.send(f"❌ {blocker} Nothing was changed.", ephemeral=True)
            return

        roster = await self.db.get_active_roster(society["id"])
        outgoing_ids = sorted({row["user_id"] for row in roster if row.get("user_id")})
        new_term_id = await self.db.confirm_handover(society["id"], interaction.user.id)
        if new_term_id is None:
            await interaction.followup.send(
                "❌ This handover was already resolved. Check `/term history`.", ephemeral=True
            )
            return

        granted, failed = await grant_alumni_roles(
            interaction.guild, active_term["academic_year"], outgoing_ids
        )
        await self._send_proclamation(
            interaction,
            society,
            concluded_term_name=active_term["term_name"],
            next_academic_year=pending["next_academic_year"],
            new_term_id=new_term_id,
            notes=pending["handover_notes"],
            alumni_summary=f"Alumni roles: {granted} granted, {failed} could not be applied.",
        )

    @app_commands.command(
        name="cancel-handover",
        description="Cancel the pending committee handover.",
    )
    async def cancel_handover(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        society, authorized = await self._check_permission(
            interaction, SocietyPermission.MANAGE_TERMS
        )
        if not society or not authorized:
            await interaction.followup.send(
                "❌ You do not have permission to cancel term handovers (`MANAGE_TERMS`).",
                ephemeral=True,
            )
            return

        cancelled = await self.db.cancel_handover(society["id"], interaction.user.id)
        message = "🛑 Pending handover cancelled." if cancelled else "❌ No handover is pending."
        await interaction.followup.send(message, ephemeral=True)

    async def _send_proclamation(
        self,
        interaction: discord.Interaction,
        society: dict,
        *,
        concluded_term_name: str,
        next_academic_year: str,
        new_term_id: int,
        notes: str | None,
        alumni_summary: str,
    ) -> None:
        color_val = 0x1E88E5
        try:
            color_val = int(society["primary_color"].lstrip("#"), 16)
        except Exception:
            pass

        soc_name = society["name"]
        embed = discord.Embed(
            title=f"🎉 Annual Handover Proclamation — {soc_name}",
            description=(
                f"The executive governance of **{soc_name}** has transitioned!\n\n"
                f"📁 **Concluded Tenure:** `{concluded_term_name}` (Archived)\n"
                f"✨ **Incoming Tenure:** `Term {next_academic_year}` "
                f"(Active, ID: {new_term_id})\n\n"
                f"**Remarks:** {notes or 'Official AGM Handover.'}\n\n"
                "👑 *Outgoing committee members are now recognized as Alumni Exco.*\n"
                f"🎓 {alumni_summary}"
            ),
            color=color_val,
        )
        embed.set_footer(text="TARClub Governance Engine • TARBot-OSS")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    db = getattr(bot, "db", None)
    if db is None:
        raise RuntimeError("Database instance not attached to bot.")
    await bot.add_cog(TermCog(bot, db))
