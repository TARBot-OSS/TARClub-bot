"""
Society registration and configuration Cog for TARClub.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from tarclub.database import Database
from tarclub.embeds import add_line_fields

logger = logging.getLogger("tarclub.cogs.society")


class SocietyApplicationReviewView(discord.ui.View):
    """Interactive button controls for Overseers to approve/reject society applications."""

    def __init__(self, bot: commands.Bot, db: Database, app_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.db = db
        self.app_id = app_id

    async def _check_authorized(self, interaction: discord.Interaction) -> bool:
        settings = getattr(self.bot, "settings", None)
        superadmins = getattr(settings, "superadmin_ids", frozenset())
        if interaction.user.id in superadmins:
            return True
        if await self.bot.is_owner(interaction.user):
            return True
        return False

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_authorized(interaction):
            await interaction.response.send_message(
                "❌ Unauthorized: Only TARClub Overseers can approve societies.", ephemeral=True
            )
            return

        await interaction.response.defer()
        app = await self.db.get_society_application(self.app_id)
        if not app or app["status"] != "PENDING":
            await interaction.followup.send(
                f"⚠️ Application #{self.app_id} is no longer pending.", ephemeral=True
            )
            return

        guild_id = app["guild_id"]
        existing = await self.db.get_society(guild_id)
        if existing:
            await interaction.followup.send(
                f"⚠️ Guild {guild_id} is already registered as **{existing['name']}**.",
                ephemeral=True,
            )
            return

        soc_id = await self.db.register_society(
            guild_id=guild_id,
            society_code=app["society_code"],
            name=app["name"],
            faculty=app["faculty"],
            initial_term="2025/2026",
            is_private=bool(app["is_private"]),
        )
        await self.db.resolve_society_application(
            app_id=self.app_id,
            status="APPROVED",
            reviewer_id=interaction.user.id,
            reason=f"Approved by {interaction.user}",
        )

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self)

        await interaction.followup.send(
            f"✅ **Application #{self.app_id} approved!** "
            f"Society ID {soc_id} provisioned for Guild {guild_id}."
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_authorized(interaction):
            await interaction.response.send_message(
                "❌ Unauthorized: Only TARClub Overseers can reject societies.", ephemeral=True
            )
            return

        await interaction.response.defer()
        app = await self.db.get_society_application(self.app_id)
        if not app or app["status"] != "PENDING":
            await interaction.followup.send(
                f"⚠️ Application #{self.app_id} is no longer pending.", ephemeral=True
            )
            return

        await self.db.resolve_society_application(
            app_id=self.app_id,
            status="REJECTED",
            reviewer_id=interaction.user.id,
            reason=f"Declined by {interaction.user}",
        )

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self)

        await interaction.followup.send(f"❌ Application #{self.app_id} rejected.")


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
        token="One-time registration token issued by TARClub Overseer (e.g. TC-89AB-4F12)",
        code="Unique club code (e.g. TARUMT-CS, TARUMT-IEESE)",
        name="Full Society Name (e.g. Computer Science Society)",
        faculty="Associated Faculty (e.g. FOCS, FOET, FAFB, FCC, CPUS, or ALL)",
        term="Initial Academic Term Year (e.g. 2025/2026)",
        is_private="True for private/internal society, False for public directory (default)",
    )
    @app_commands.default_permissions(administrator=True)
    async def register(
        self,
        interaction: discord.Interaction,
        token: str | None = None,
        code: str | None = None,
        name: str | None = None,
        faculty: str | None = None,
        term: str = "2025/2026",
        is_private: bool = False,
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

        # If token is provided, resolve registration from token metadata
        if token:
            tok_data = await self.db.get_society_token(token)
            if not tok_data or tok_data.get("status") != "ACTIVE":
                await interaction.followup.send(
                    "❌ Invalid, expired, or already-redeemed registration token.\n"
                    "Please check the token code or contact your TARClub Overseer.",
                    ephemeral=True,
                )
                return
            code = tok_data["society_code"]
            name = tok_data["name"]
            faculty = tok_data["faculty"]
            is_private = bool(tok_data.get("is_private", 0))
        else:
            if not code or not name:
                await interaction.followup.send(
                    "❌ Missing details. Please provide either a valid registration token "
                    "(`token:TC-XXXX`)\nor specify both `code` and `name`.\n"
                    "💡 Alternatively, use `/society apply` to submit a registration application!",
                    ephemeral=True,
                )
                return
            faculty = faculty or "ALL"

        try:
            society_id = await self.db.register_society(
                guild_id=interaction.guild_id,
                society_code=code,
                name=name,
                faculty=faculty,
                initial_term=term,
                is_private=is_private,
            )
            if token:
                await self.db.redeem_society_token(
                    token=token,
                    guild_id=interaction.guild_id,
                    user_id=interaction.user.id,
                )
        except Exception as e:
            logger.exception("Failed to register society for guild %s", interaction.guild_id)
            await interaction.followup.send(f"❌ Failed to register society: {e}", ephemeral=True)
            return

        positions = await self.db.get_positions(society_id)
        vis_label = "🔒 **Private / Internal**" if is_private else "🌐 **Public Directory**"

        embed = discord.Embed(
            title=f"🏛️ Society Registered: {name}",
            description=(
                f"**Code:** `{code.upper()}`\n"
                f"**Faculty:** `{faculty.upper()}`\n"
                f"**Visibility:** {vis_label}\n"
                f"**Active Term:** `Term {term}`\n\n"
                f"✅ **15 Standard Positions Provisioned!**\n"
                f"• Top Hierarchies: {', '.join([p['title'] for p in positions[:4]])}\n"
                f"• General Membership: `{positions[-1]['title']}`\n\n"
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
        name="apply",
        description="Apply to register this server as an official TAR UMT society/club.",
    )
    @app_commands.describe(
        code="Unique club code (e.g. TARUMT-CS, TARUMT-ROBO)",
        name="Full Society Name (e.g. Robotics Club)",
        faculty="Associated Faculty (e.g. FOCS, FOET, FAFB, FCC, CPUS, or ALL)",
        description="Brief purpose, activities, or background of the society",
        is_private="True for private/internal society, False for public directory (default)",
    )
    @app_commands.default_permissions(administrator=True)
    async def apply(
        self,
        interaction: discord.Interaction,
        code: str,
        name: str,
        faculty: str = "ALL",
        description: str = "",
        is_private: bool = False,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        existing = await self.db.get_society(interaction.guild_id)
        if existing:
            soc_title = f"{existing['name']} ({existing['society_code']})"
            await interaction.followup.send(
                f"⚠️ This server is already registered as **{soc_title}**.",
                ephemeral=True,
            )
            return

        app_id = await self.db.create_society_application(
            guild_id=interaction.guild_id,
            guild_name=interaction.guild.name,
            applicant_id=interaction.user.id,
            applicant_name=str(interaction.user),
            code=code,
            name=name,
            faculty=faculty,
            description=description,
            is_private=is_private,
        )

        vis_str = "🔒 Private" if is_private else "🌐 Public Directory"

        # Route review notification to review channel or bot owner DM
        review_chan_id = getattr(getattr(self.bot, "settings", None), "review_channel_id", None)
        target_chan = self.bot.get_channel(review_chan_id) if review_chan_id else None

        review_embed = discord.Embed(
            title=f"📥 New Society Application: #{app_id}",
            description=(
                f"**Society Name:** {name}\n"
                f"**Code:** `{code.upper()}`\n"
                f"**Faculty:** `{faculty.upper()}`\n"
                f"**Visibility:** {vis_str}\n"
                f"**Server:** {interaction.guild.name} (`{interaction.guild.id}`)\n"
                f"**Applicant:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"**Description:** {description or '*None provided*'}\n\n"
                f"🎮 *Can also be approved via host terminal: `approve {app_id}`*"
            ),
            color=0xF1C40F,
        )
        review_embed.set_footer(text="TARClub Overseer Review System")

        view = SocietyApplicationReviewView(self.bot, self.db, app_id)

        dispatched = False
        if target_chan and isinstance(target_chan, discord.TextChannel):
            try:
                await target_chan.send(embed=review_embed, view=view)
                dispatched = True
            except Exception as e:
                logger.warning("Failed to dispatch application to review channel: %s", e)

        if not dispatched:
            app_info = await self.bot.application_info()
            if app_info.owner:
                try:
                    await app_info.owner.send(embed=review_embed, view=view)
                    dispatched = True
                except Exception as e:
                    logger.warning("Failed to dispatch application to bot owner DM: %s", e)

        print(
            f"\n[APPLICATION] New society request #{app_id}: {name} ({code.upper()}) | "
            f"Guild: {interaction.guild.name} ({interaction.guild.id}) | "
            f"Approve via terminal: approve {app_id}\n"
        )

        await interaction.followup.send(
            f"✅ **Application #{app_id} submitted successfully!**\n"
            f"Request for **{name}** (`{code.upper()}`) forwarded to TARClub Overseers for review.",
            ephemeral=True,
        )

    @app_commands.command(
        name="set-privacy",
        description="Toggle whether this society is listed publicly in the directory.",
    )
    @app_commands.describe(is_private="True to make private/hidden, False for public directory")
    @app_commands.default_permissions(administrator=True)
    async def set_privacy(self, interaction: discord.Interaction, is_private: bool) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        society = await self.db.get_society(interaction.guild_id)
        if not society:
            await interaction.followup.send("❌ Society not registered.", ephemeral=True)
            return

        await self.db.set_society_privacy(interaction.guild_id, is_private)
        mode_text = (
            "🔒 **PRIVATE** (Hidden from public directory)"
            if is_private
            else "🌐 **PUBLIC** (Discoverable in directory)"
        )
        await interaction.followup.send(
            f"✅ Society visibility updated to {mode_text}.",
            ephemeral=False,
        )

    @app_commands.command(
        name="directory",
        description="Browse the public directory of registered TAR UMT societies.",
    )
    async def directory(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=False)
        societies = await self.db.list_public_societies()

        if not societies:
            await interaction.followup.send(
                "ℹ️ No public societies are currently listed in the directory.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🏛️ TAR UMT Public Societies Directory",
            description=f"Showing **{len(societies)}** public societies & clubs across TAR UMT:\n",
            color=0x1E88E5,
        )

        faculties: dict[str, list[str]] = {}
        for s in societies:
            fac = s.get("faculty", "GENERAL").upper()
            badge = s.get("emoji_badge") or "🏛️"
            entry = f"• {badge} **{s['name']}** (`{s['society_code']}`)"
            faculties.setdefault(fac, []).append(entry)

        complete = True
        for fac_name, entries in faculties.items():
            if not add_line_fields(embed, f"Faculty / Group: {fac_name}", entries):
                complete = False

        footer = "TARClub Multi-Tenant Society Engine • TARBot-OSS"
        if not complete:
            footer += " • Directory truncated to fit Discord embed limits"
        embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed)

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
        vis_badge = "🔒 Private" if society.get("is_private") else "🌐 Public"

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
                f"**Visibility:** `{vis_badge}`\n"
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
