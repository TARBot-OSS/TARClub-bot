"""
Committee roster and position management Cog for TARClub.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from tarclub.database import Database
from tarclub.services.integration_service import IntegrationService
from tarclub.services.rbac_service import SocietyPermission, has_permission

if TYPE_CHECKING:
    pass

logger = logging.getLogger("tarclub.cogs.committee")


class CommitteeCog(commands.GroupCog, group_name="committee"):
    """Commands for viewing and managing society committee rosters and positions."""

    def __init__(self, bot: commands.Bot, db: Database) -> None:
        self.bot = bot
        self.db = db
        super().__init__()

    async def _check_permission(
        self, interaction: discord.Interaction, required_perm: SocietyPermission
    ) -> tuple[dict | None, bool]:
        """Check if user is server owner, admin, or has society permission."""
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

    async def position_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete available society positions."""
        if not interaction.guild_id:
            return []
        society = await self.db.get_society(interaction.guild_id)
        if not society:
            return []
        positions = await self.db.get_positions(society["id"])
        choices: list[app_commands.Choice[str]] = []
        for p in positions:
            if current.lower() in p["title"].lower():
                choice_label = f"{p['title']} ({p['department']})"
                choices.append(app_commands.Choice(name=choice_label, value=p["title"]))
                if len(choices) >= 25:
                    break
        return choices

    @app_commands.command(
        name="roster",
        description="Display the official committee roster for the active term.",
    )
    async def roster(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=False)
        society = await self.db.get_society(interaction.guild_id)
        if not society:
            await interaction.followup.send(
                "❌ This server is not yet registered. Run `/society register` first!",
                ephemeral=True,
            )
            return

        active_term = await self.db.get_active_term(society["id"])
        term_label = active_term["term_name"] if active_term else "Current Term"
        roster_data = await self.db.get_active_roster(society["id"])

        color_val = 0x1E88E5
        try:
            color_val = int(society["primary_color"].lstrip("#"), 16)
        except Exception:
            pass

        embed = discord.Embed(
            title=f"{society.get('emoji_badge', '🏛️')} {society['name']} — Committee Roster",
            description=f"**Tenure:** `{term_label}` | **Faculty:** `{society['faculty']}`\n",
            color=color_val,
        )

        departments: dict[str, list[str]] = {
            "PRESIDIUM": [],
            "SECRETARIAT": [],
            "FINANCE": [],
            "DIRECTORSHIP": [],
            "OTHER": [],
        }

        for row in roster_data:
            dept = row["department"] if row["department"] in departments else "OTHER"
            holder = f"<@{row['user_id']}>" if row.get("user_id") else "*Vacant*"
            verified_tag = " `[Verified Student]`" if row.get("is_verified_student") else ""
            departments[dept].append(f"• **{row['title']}:** {holder}{verified_tag}")

        dept_labels = {
            "PRESIDIUM": "👑 Presidium & High Council",
            "SECRETARIAT": "📝 Secretariat",
            "FINANCE": "💰 Treasury & Finance",
            "DIRECTORSHIP": "🎯 Directorships & Operations",
            "OTHER": "📌 Additional Positions",
        }

        for dept_key, items in departments.items():
            if items:
                embed.add_field(
                    name=dept_labels[dept_key],
                    value="\n".join(items),
                    inline=False,
                )

        if society.get("banner_url"):
            embed.set_image(url=society["banner_url"])

        embed.set_footer(text=f"Total Positions: {len(roster_data)} • TARClub Governance")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="assign", description="Assign a member to a committee position.")
    @app_commands.describe(
        member="The server member to appoint",
        position="Position title",
    )
    @app_commands.autocomplete(position=position_autocomplete)
    async def assign(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        position: str,
    ) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        society, authorized = await self._check_permission(
            interaction, SocietyPermission.ASSIGN_COMMITTEE
        )
        if not society:
            await interaction.followup.send("❌ Society not registered.", ephemeral=True)
            return
        if not authorized:
            await interaction.followup.send(
                "❌ You do not have permission to assign committee members (`ASSIGN_COMMITTEE`).",
                ephemeral=True,
            )
            return

        active_term = await self.db.get_active_term(society["id"])
        if not active_term:
            await interaction.followup.send("❌ No active term found.", ephemeral=True)
            return

        positions = await self.db.get_positions(society["id"])
        target_pos = next((p for p in positions if p["title"].lower() == position.lower()), None)
        if not target_pos:
            await interaction.followup.send(
                f"❌ Position **{position}** does not exist.", ephemeral=True
            )
            return

        # Check TARVeri student verification gating
        integration = await self.db.get_integration(interaction.guild_id)
        enforce_gating = (
            bool(integration.get("enforce_student_verification", 1)) if integration else True
        )
        settings = getattr(self.bot, "settings", None)
        tarveri_db = getattr(settings, "tarveri_db_path", None)
        is_verified, role_label = IntegrationService.verify_member_student_status(
            member, tarveri_db_path=tarveri_db
        )

        if enforce_gating and not is_verified:
            # Check if caller has bypass permission
            user_mask = await self.db.get_user_permissions(society["id"], interaction.user.id)
            has_bypass = has_permission(user_mask, SocietyPermission.BYPASS_STUDENT_GATE)
            is_owner = interaction.user.id == interaction.guild.owner_id
            if not has_bypass and not is_owner:
                await interaction.followup.send(
                    f"❌ Cannot appoint {member.mention}: Committee members must be "
                    "verified active TAR UMT students through TARVeri.",
                    ephemeral=True,
                )
                return

        # Appoint in database
        await self.db.assign_committee_member(
            term_id=active_term["id"],
            position_id=target_pos["id"],
            user_id=member.id,
            is_verified=is_verified,
        )

        active_label = active_term["term_name"]
        soc_pos_title = target_pos["title"]
        v_tag = " `[Verified Student]`" if is_verified else ""
        msg = f"✅ Appointed {member.mention} as **{soc_pos_title}** (`{active_label}`){v_tag}."
        await interaction.followup.send(msg, ephemeral=False)

    @app_commands.command(name="relieve", description="Relieve a member from a committee position.")
    @app_commands.describe(
        member="The member to relieve",
        position="Position title",
    )
    @app_commands.autocomplete(position=position_autocomplete)
    async def relieve(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        position: str,
    ) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        society, authorized = await self._check_permission(
            interaction, SocietyPermission.ASSIGN_COMMITTEE
        )
        if not society or not authorized:
            await interaction.followup.send(
                "❌ Unauthorized to relieve committee members.", ephemeral=True
            )
            return

        active_term = await self.db.get_active_term(society["id"])
        if not active_term:
            await interaction.followup.send("❌ No active term found.", ephemeral=True)
            return

        positions = await self.db.get_positions(society["id"])
        target_pos = next((p for p in positions if p["title"].lower() == position.lower()), None)
        if not target_pos:
            await interaction.followup.send(
                f"❌ Position **{position}** not found.", ephemeral=True
            )
            return

        pos_title = target_pos["title"]
        removed = await self.db.relieve_committee_member(
            active_term["id"], target_pos["id"], member.id
        )
        if removed:
            await interaction.followup.send(
                f"✅ Relieved {member.mention} from **{pos_title}**.", ephemeral=False
            )
        else:
            await interaction.followup.send(
                f"⚠️ {member.mention} does not currently hold **{pos_title}**.", ephemeral=True
            )

    @app_commands.command(
        name="add-position",
        description="Add a new custom position to the society hierarchy.",
    )
    @app_commands.describe(
        title="Position title (e.g. Esports Coordinator)",
        department="Department (PRESIDIUM, SECRETARIAT, FINANCE, DIRECTORSHIP, or OTHER)",
        hierarchy="Hierarchy rank number (1-20)",
    )
    async def add_position(
        self,
        interaction: discord.Interaction,
        title: str,
        department: str,
        hierarchy: int = 15,
    ) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        society, authorized = await self._check_permission(
            interaction, SocietyPermission.MANAGE_POSITIONS
        )
        if not society or not authorized:
            await interaction.followup.send(
                "❌ Unauthorized to manage committee positions.", ephemeral=True
            )
            return

        try:
            pos_id = await self.db.add_custom_position(
                society_id=society["id"],
                title=title,
                department=department,
                hierarchy_order=hierarchy,
                rbac_flags=SocietyPermission.DIRECTOR_CORE.value,
            )
            dept_name = department.upper()
            await interaction.followup.send(
                f"✅ Added position **{title}** (`{dept_name}`, Rank #{hierarchy}, ID: {pos_id})!",
                ephemeral=False,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to add position: {e}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    db = getattr(bot, "db", None)
    if db is None:
        raise RuntimeError("Database instance not attached to bot.")
    await bot.add_cog(CommitteeCog(bot, db))
