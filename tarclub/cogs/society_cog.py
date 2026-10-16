"""
Society registration and configuration Cog for TARClub.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from tarclub.database import Database

logger = logging.getLogger("tarclub.cogs.society")


class SocietyCog(commands.GroupCog, group_name="society"):
    """Commands for registering and managing society profiles, branding, and themes."""

    def __init__(self, bot: commands.Bot, db: Database) -> None:
        self.bot = bot
        self.db = db
        super().__init__()

    @app_commands.command(
        name="register",
        description="Register this server as an official TAR UMT society/club.",
    )
    @app_commands.describe(
        code="Unique club code (e.g. TARUMT-CS, TARUMT-IEESE)",
        name="Full Society Name (e.g. Computer Science Society)",
        faculty="Associated Faculty (e.g. FOCS, FOET, FAFB, FCC, CPUS, or ALL)",
        term="Initial Academic Term Year (e.g. 2025/2026)",
    )
    @app_commands.default_permissions(administrator=True)
    async def register(
        self,
        interaction: discord.Interaction,
        code: str,
        name: str,
        faculty: str,
        term: str = "2025/2026",
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        existing = await self.db.get_society(interaction.guild_id)
        if existing:
            soc_name = existing["name"]
            soc_code = existing["society_code"]
            await interaction.followup.send(
                f"⚠️ This server is already registered as **{soc_name}** (`{soc_code}`).\n"
                "Use `/society info` to view details or `/society theme` to customize branding.",
                ephemeral=True,
            )
            return

        try:
            society_id = await self.db.register_society(
                guild_id=interaction.guild_id,
                society_code=code,
                name=name,
                faculty=faculty,
                initial_term=term,
            )
        except Exception as e:
            logger.exception("Failed to register society for guild %s", interaction.guild_id)
            await interaction.followup.send(f"❌ Failed to register society: {e}", ephemeral=True)
            return

        positions = await self.db.get_positions(society_id)

        embed = discord.Embed(
            title=f"🏛️ Society Registered: {name}",
            description=(
                f"**Code:** `{code.upper()}`\n"
                f"**Faculty:** `{faculty.upper()}`\n"
                f"**Active Term:** `Term {term}`\n\n"
                f"✅ **14 Standard Positions Provisioned!**\n"
                f"• Top Hierarchies: {', '.join([p['title'] for p in positions[:4]])}\n"
                f"• Directorships: {', '.join([p['title'] for p in positions[8:12]])}...\n\n"
                "💡 **Next Steps:**\n"
                "1. Assign members: `/committee assign`\n"
                "2. Customize roles: `/committee edit-position`\n"
                "3. Set brand colors: `/society theme`"
            ),
            color=0x1E88E5,
        )
        embed.set_footer(text="TARClub Society Management • TAR UMT")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(
        name="info",
        description="View the society profile, theme, and active term.",
    )
    async def info(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        society = await self.db.get_society(interaction.guild_id)

        if not society:
            await interaction.followup.send(
                "❌ This server is not yet registered as a society. Run `/society register`!",
                ephemeral=True,
            )
            return

        active_term = await self.db.get_active_term(society["id"])
        term_name = active_term["term_name"] if active_term else "No Active Term"

        # Parse primary color
        color_val = 0x1E88E5
        try:
            color_val = int(society["primary_color"].lstrip("#"), 16)
        except Exception:
            pass

        embed = discord.Embed(
            title=f"{society.get('emoji_badge', '🏛️')} {society['name']}",
            description=(
                f"**Society Code:** `{society['society_code']}`\n"
                f"**Faculty:** `{society['faculty']}`\n"
                f"**Current Term:** `{term_name}`\n"
                f"**Brand Color:** `{society['primary_color']}`"
            ),
            color=color_val,
        )
        if society.get("banner_url"):
            embed.set_image(url=society["banner_url"])

        embed.set_footer(text="TARClub Governance Engine • TARBot-OSS")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(
        name="theme",
        description="Customize the branding, colors, emoji, and banners for this society.",
    )
    @app_commands.describe(
        primary_color="Primary brand hex code (e.g. #1E88E5 or #E53935)",
        secondary_color="Accent brand hex code (e.g. #FFC107)",
        emoji_badge="Custom or Unicode emoji badge for embeds (e.g. 💻, 🚀, 🎭)",
        banner_url="Direct image URL for official announcements and roster banners",
        footer_text="Custom embed footer text",
    )
    async def theme(
        self,
        interaction: discord.Interaction,
        primary_color: str | None = None,
        secondary_color: str | None = None,
        emoji_badge: str | None = None,
        banner_url: str | None = None,
        footer_text: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        society = await self.db.get_society(interaction.guild_id)
        if not society:
            await interaction.followup.send("❌ Society not registered.", ephemeral=True)
            return

        # Sanitize hex colors if provided
        if primary_color and not primary_color.startswith("#"):
            primary_color = f"#{primary_color}"
        if secondary_color and not secondary_color.startswith("#"):
            secondary_color = f"#{secondary_color}"

        await self.db.update_theme(
            society_id=society["id"],
            primary_color=primary_color,
            secondary_color=secondary_color,
            banner_url=banner_url,
            emoji_badge=emoji_badge,
            footer_text=footer_text,
        )

        updated = await self.db.get_society(interaction.guild_id)
        assert updated is not None

        color_val = 0x1E88E5
        try:
            color_val = int(updated["primary_color"].lstrip("#"), 16)
        except Exception:
            pass

        embed = discord.Embed(
            title=f"🎨 Theme Updated: {updated['name']}",
            description=(
                f"**Primary Color:** `{updated['primary_color']}`\n"
                f"**Secondary Color:** `{updated.get('secondary_color', '#FFC107')}`\n"
                f"**Emoji Badge:** {updated.get('emoji_badge', '🏛️')}\n"
                f"**Banner:** {updated.get('banner_url') or '*None*'}"
            ),
            color=color_val,
        )
        if updated.get("banner_url"):
            embed.set_image(url=updated["banner_url"])

        embed.set_footer(text=updated.get("footer_text") or "TARClub Theming Engine")
        await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot) -> None:
    db = getattr(bot, "db", None)
    if db is None:
        raise RuntimeError("Database instance not attached to bot.")
    await bot.add_cog(SocietyCog(bot, db))
