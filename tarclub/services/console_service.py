"""
Interactive Console CLI Service for TARClub (Minecraft-style Terminal Backend).

Allows bot hosters and administrators to execute root management commands,
generate single-use society registration tokens, approve/reject applications,
audit registered societies, and broadcast messages directly from the terminal/tmux
session with full scrollback support.
The TUI, PAM login and session handling live in the shared `tarbot_console` package.
"""

from __future__ import annotations

import logging
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from tarbot_console import ConsoleAwareStreamHandler, ConsoleCore

if TYPE_CHECKING:
    from tarclub.bot import TARClubBot
    from tarclub.config import Settings
    from tarclub.database import Database

logger = logging.getLogger("tarclub.console")


AVAILABLE_COMMANDS = [
    "help",
    "status",
    "guilds",
    "societies",
    "privacy",
    "token",
    "applications",
    "approve",
    "reject",
    "say",
    "eval",
    "login",
    "setup-admin",
    "lock",
    "logout",
    "user",
    "view",
    "minimize",
    "restore",
    "clear",
    "stop",
    "exit",
    "quit",
]

AUTH_LOCKED_COMMANDS = [
    "help",
    "?",
    "login",
    "setup-admin",
    "view",
    "minimize",
    "restore",
    "clear",
    "stop",
    "exit",
    "quit",
]

FACULTY_CODES = [
    "FOCS",
    "FAFB",
    "FOET",
    "FOAS",
    "FOBE",
    "FCCI",
    "FSSH",
    "CPUS",
]

HELP_LINES = [
    "======================= TARClub Console Commands =======================",
    "  help, ?                               Show this interactive help reference",
    "  login <username>                      Authenticate via Linux PAM to unlock console",
    "  setup-admin <username>                Initialize the first console administrator",
    "  lock, logout                          Lock console session",
    "  user <list|add|remove> [name]         Manage authorized console administrators",
    "  view [split|logs|output]              Switch UI layout view (F2/F3 shortcuts)",
    "  minimize [output|logs]                Minimize terminal output or logs pane",
    "  restore [all|output|logs]             Restore minimized panes to split view",
    "  status                                View bot latency, connected guilds, societies",
    "  guilds                                List connected Discord servers",
    "  societies                             List all registered societies and visibility",
    "  privacy <guild_id> <true|false>       Toggle public directory visibility",
    "  token create <code> <name> [fac] [p]  Generate single-use society registration token",
    "  token list [active|used|revoked]      List registration tokens",
    "  token revoke <token>                  Revoke an unused registration token",
    "  applications [status]                 List pending/resolved society applications",
    "  approve <app_id>                      Approve application & provision server",
    "  reject <app_id> [reason]              Reject application with reason",
    "  say <guild_id> <chan_id> <msg>        Send message to a channel as TARClub",
    "  eval <code>                           Execute Python expression/statement",
    "  clear [output|logs|all]               Clear terminal screen or logs",
    "  stop, exit, quit                      Gracefully shut down TARClub",
    "  • Hotkeys: F2/Ctrl+O (Toggle Output), F3 (Toggle Logs), PgUp/PgDn (Scroll)",
    "========================================================================",
]


def _console_completer(text: str, state: int) -> str | None:
    try:
        import readline

        buf = readline.get_line_buffer()
        begidx = readline.get_begidx()
        text_before = buf[:begidx].strip()
        try:
            words = shlex.split(text_before)
        except ValueError:
            words = text_before.split()
    except Exception:
        words = []

    matches: list[str] = []
    if not words:
        matches = [c for c in AVAILABLE_COMMANDS if c.startswith(text.lower())]
    elif words == ["token"]:
        matches = [sub for sub in ["create", "list", "revoke"] if sub.startswith(text.lower())]
    elif words == ["token", "list"]:
        matches = [
            opt for opt in ["active", "used", "revoked", "all"] if opt.startswith(text.lower())
        ]
    elif words == ["user"]:
        matches = [opt for opt in ["list", "add", "remove"] if opt.startswith(text.lower())]
    elif len(words) == 2 and words[0] == "privacy":
        matches = [opt for opt in ["true", "false"] if opt.startswith(text.lower())]
    elif words == ["applications"]:
        matches = [
            opt
            for opt in ["pending", "approved", "rejected", "all"]
            if opt.startswith(text.lower())
        ]
    elif len(words) == 4 and words[0] == "token" and words[1] == "create":
        matches = [f for f in FACULTY_CODES if f.startswith(text.upper())]
    elif len(words) >= 5 and words[0] == "token" and words[1] == "create":
        matches = [opt for opt in ["true", "false"] if opt.startswith(text.lower())]

    if state < len(matches):
        return matches[state]
    return None



class ConsoleService(ConsoleCore):
    """Split-Screen admin TUI for TARClub."""

    bot_name = "TARClub"
    history_file = "logs/.tarclub_history"
    help_lines = HELP_LINES
    alert_channel_names = frozenset(
        {"tarclub-log", "tarclub_log", "tarveri-log", "tarveri_log", "admin-log", "mod-log"}
    )
    extra_completions = {
        "status": None,
        "guilds": None,
        "societies": None,
        "privacy": None,
        "token": {
            "create": None,
            "list": {"active": None, "used": None, "revoked": None},
            "revoke": None,
        },
        "applications": None,
        "approve": None,
        "reject": None,
    }

    def __init__(self, bot: TARClubBot, db: Database, settings: Settings) -> None:
        super().__init__(bot, db, settings)

    async def dispatch_command(self, cmd: str, args: list[str]) -> bool:
        if cmd == "status":
            await self._cmd_status()
        elif cmd == "guilds":
            await self._cmd_guilds()
        elif cmd == "societies":
            await self._cmd_societies()
        elif cmd == "privacy":
            await self._cmd_privacy(args)
        elif cmd == "token":
            await self._cmd_token(args)
        elif cmd == "applications":
            await self._cmd_applications()
        elif cmd == "approve":
            await self._cmd_approve(args)
        elif cmd == "reject":
            await self._cmd_reject(args)
        else:
            return False
        return True

    def _before_start(self) -> None:
        # Attach ConsoleAwareStreamHandler to root and tarclub loggers,
        # and ensure file handler records logs
        try:
            from logging.handlers import RotatingFileHandler

            root_logger = logging.getLogger()
            fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s : %(message)s")
            handler = ConsoleAwareStreamHandler(sys.stdout)
            handler.setFormatter(fmt)
            handlers_to_remove = [
                h for h in root_logger.handlers if type(h) is logging.StreamHandler
            ]
            for h in handlers_to_remove:
                root_logger.removeHandler(h)
            root_logger.addHandler(handler)

            logs_dir = Path("logs")
            logs_dir.mkdir(parents=True, exist_ok=True)
            has_file_handler = any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
            if not has_file_handler:
                file_handler = RotatingFileHandler(
                    logs_dir / "tarclub.log",
                    maxBytes=2_000_000,
                    backupCount=5,
                    encoding="utf-8",
                )
                file_handler.setFormatter(fmt)
                root_logger.addHandler(file_handler)
        except Exception as e:
            self.log.debug("Could not attach ConsoleAwareStreamHandler: %s", e)

    async def _alert_before(self, embed: discord.Embed) -> None:
        rev_chan_id = getattr(self.settings, "review_channel_id", None)
        if not rev_chan_id:
            return
        chan = self.bot.get_channel(rev_chan_id)
        if not chan:
            try:
                chan = await self.bot.fetch_channel(rev_chan_id)
            except Exception as exc:
                self.log.debug("Failed fetching review channel: %s", exc)
        if chan and isinstance(chan, discord.TextChannel):
            try:
                await chan.send(embed=embed)
            except Exception as exc:
                self.log.debug("Could not send alert to review channel: %s", exc)

    async def _cmd_status(self) -> None:
        latency_ms = round(self.bot.latency * 1000, 1) if self.bot.latency else 0
        guilds_count = len(self.bot.guilds)

        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM societies;")
        soc_count = (await cursor.fetchone())[0]

        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM terms WHERE is_active = 1;")
        active_terms = (await cursor.fetchone())[0]

        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM society_tokens WHERE status = 'ACTIVE';"
        )
        active_tokens = (await cursor.fetchone())[0]

        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM society_applications WHERE status = 'PENDING';"
        )
        pending_apps = (await cursor.fetchone())[0]

        usr_tag = str(self.bot.user) if self.bot.user else "N/A"
        usr_id = self.bot.user.id if self.bot.user else "N/A"
        lines = [
            f"[STATUS] Bot User: {usr_tag} (ID: {usr_id})",
            f"[STATUS] WebSocket Ping: {latency_ms} ms | Guilds: {guilds_count}",
            f"[STATUS] Registered Societies: {soc_count} | Active Terms: {active_terms}",
            f"[STATUS] Active Tokens: {active_tokens} | Pending Applications: {pending_apps}",
            f"[STATUS] Database: {self.settings.database_path}",
        ]
        self.write_output("\n".join(lines))

    async def _cmd_guilds(self) -> None:
        if not self.bot.guilds:
            self.write_output("[INFO] No connected guilds.")
            return

        lines = [f"\n--- Connected Guilds ({len(self.bot.guilds)}) ---"]
        for g in self.bot.guilds:
            soc = await self.db.get_society(g.id)
            soc_tag = f"[{soc['society_code']}] {soc['name']}" if soc else "(Not Registered)"
            lines.append(
                f"• [{g.id}] {g.name:<30} | Members: {g.member_count:<5} | Society: {soc_tag}"
            )
        lines.append("------------------------------------------------")
        self.write_output("\n".join(lines))

    async def _cmd_societies(self) -> None:
        cursor = await self.db.conn.execute("SELECT * FROM societies ORDER BY id ASC;")
        rows = await cursor.fetchall()
        if not rows:
            self.write_output("[INFO] No societies registered yet.")
            return

        lines = [f"\n--- Registered Societies ({len(rows)}) ---"]
        for r in rows:
            soc = dict(r)
            vis = "🔒 Private" if soc.get("is_private") else "🌐 Public "
            lines.append(
                f"• ID: {soc['id']:<2} | Code: {soc['society_code']:<12} | "
                f"Faculty: {soc['faculty']:<6} | {vis} | Guild: {soc['guild_id']}\n"
                f"  Name: {soc['name']}"
            )
        lines.append("------------------------------------------")
        self.write_output("\n".join(lines))

    async def _cmd_privacy(self, args: list[str]) -> None:
        if len(args) < 2:
            self.write_output("[WARN] Usage: privacy <guild_id> <true|false>")
            return

        g_id_raw, val_raw = args[0], args[1].lower()
        if not g_id_raw.isdigit():
            self.write_output("[ERROR] guild_id must be numeric.")
            return

        g_id = int(g_id_raw)
        is_private = val_raw in ("true", "1", "yes", "private")

        success = await self.db.set_society_privacy(g_id, is_private)
        if success:
            vis_text = (
                "Private (Hidden from directory)" if is_private else "Public (Listed in directory)"
            )
            self.write_output(f"[SUCCESS] Guild {g_id} privacy updated to: {vis_text}")
        else:
            self.write_output(f"[WARN] No society found registered under Guild ID {g_id}.")

    async def _cmd_token(self, args: list[str]) -> None:
        if not args:
            self.write_output("[WARN] Usage: token <create|list|revoke> [args...]")
            return

        sub = args[0].lower()
        if sub == "create":
            if len(args) < 3:
                self.write_output("[WARN] Usage: token create <code> <name> [faculty] [is_private]")
                return
            code = args[1].upper()
            name = args[2]
            faculty = args[3].upper() if len(args) > 3 else "ALL"
            is_private = (
                args[4].lower() in ("true", "1", "yes", "private") if len(args) > 4 else False
            )

            token = await self.db.create_society_token(
                society_code=code,
                name=name,
                faculty=faculty,
                is_private=is_private,
            )
            banner = [
                "\n========================================================",
                f"🎉 Generated Registration Token: {token}",
                f"• Code:       {code}",
                f"• Name:       {name}",
                f"• Faculty:    {faculty}",
                f"• Visibility: {'Private' if is_private else 'Public'}",
                "• Instruction: Give this token to the club president.",
                f"  They run: `/society register token:{token}` in their server!",
                "========================================================\n",
            ]
            self.write_output("\n".join(banner))

        elif sub == "list":
            status_filter = args[1].upper() if len(args) > 1 else None
            tokens = await self.db.list_society_tokens(status=status_filter)
            if not tokens:
                self.write_output(
                    f"[INFO] No tokens found matching status: {status_filter or 'ALL'}"
                )
                return

            lines = [f"\n--- Society Registration Tokens ({len(tokens)}) ---"]
            for t in tokens:
                status_badge = f"[{t['status']}]"
                used_by = (
                    f"Guild {t['used_by_guild_id']}" if t.get("used_by_guild_id") else "Unused"
                )
                lines.append(
                    f"• {t['token']} {status_badge:<9} | Code: {t['society_code']:<12} | "
                    f"{t['name']:<25} | {used_by}"
                )
            lines.append("-----------------------------------------------------")
            self.write_output("\n".join(lines))

        elif sub == "revoke":
            if len(args) < 2:
                self.write_output("[WARN] Usage: token revoke <token>")
                return
            tok = args[1].strip().upper()
            success = await self.db.revoke_society_token(tok)
            if success:
                self.write_output(f"[SUCCESS] Token {tok} has been revoked.")
            else:
                self.write_output(f"[WARN] Token {tok} not found or was not in ACTIVE status.")
        else:
            self.write_output(
                f"[WARN] Unknown token subcommand '{sub}'. Use create, list, or revoke."
            )

    async def _cmd_applications(self) -> None:
        apps = await self.db.list_society_applications(status="PENDING")
        if not apps:
            self.write_output("[INFO] No pending society applications.")
            return

        lines = [f"\n--- Pending Society Applications ({len(apps)}) ---"]
        for a in apps:
            vis = "Private" if a.get("is_private") else "Public"
            lines.append(
                f"• [ID: {a['id']}] Code: {a['society_code']} | Name: {a['name']} | "
                f"Faculty: {a['faculty']} | {vis}\n"
                f"  Requester: {a['applicant_name']} (User ID: {a['applicant_id']}) | "
                f"Guild: {a['guild_name']} (ID: {a['guild_id']})\n"
                f"  To approve: approve {a['id']}\n"
                f"  To reject:  reject {a['id']} [reason]"
            )
        lines.append("---------------------------------------------------")
        self.write_output("\n".join(lines))

    async def _cmd_approve(self, args: list[str]) -> None:
        if not args:
            self.write_output("[WARN] Usage: approve <application_id>")
            return

        app_id_raw = args[0]
        if not app_id_raw.isdigit():
            self.write_output("[ERROR] application_id must be numeric.")
            return

        app_id = int(app_id_raw)
        application = await self.db.get_society_application(app_id)
        if not application:
            self.write_output(f"[ERROR] Application ID {app_id} not found.")
            return

        if application["status"] != "PENDING":
            self.write_output(f"[WARN] Application ID {app_id} is already {application['status']}.")
            return

        guild_id = application["guild_id"]
        existing = await self.db.get_society(guild_id)
        if existing:
            self.write_output(
                f"[ERROR] Guild {guild_id} is already registered as {existing['name']}."
            )
            return

        try:
            soc_id = await self.db.register_society(
                guild_id=guild_id,
                society_code=application["society_code"],
                name=application["name"],
                faculty=application["faculty"],
                initial_term="2025/2026",
                is_private=bool(application["is_private"]),
            )
            await self.db.resolve_society_application(
                app_id=app_id,
                status="APPROVED",
                reviewer_id=self.bot.user.id if self.bot.user else 0,
                reason="Approved via host terminal console",
            )
            self.write_output(
                f"[SUCCESS] Application #{app_id} APPROVED! "
                f"Society ID {soc_id} provisioned for Guild {guild_id}."
            )

            guild = self.bot.get_guild(guild_id)
            if guild:
                channel = guild.system_channel or next(
                    (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
                    None,
                )
                if channel:
                    embed = discord.Embed(
                        title=f"🏛️ Society Approved: {application['name']}",
                        description=(
                            f"Congratulations! Your application for **{application['name']}** "
                            f"(`{application['society_code']}`) was approved by Overseers.\n\n"
                            "✅ Standard 15 positions have been provisioned.\n"
                            "Use `/committee assign` and `/society theme` to get started!"
                        ),
                        color=0x2ECC71,
                    )
                    await channel.send(embed=embed)
        except Exception as e:
            self.write_output(f"[ERROR] Failed to approve application: {e}")

    async def _cmd_reject(self, args: list[str]) -> None:
        if not args:
            self.write_output("[WARN] Usage: reject <application_id> [reason]")
            return

        app_id_raw = args[0]
        if not app_id_raw.isdigit():
            self.write_output("[ERROR] application_id must be numeric.")
            return

        app_id = int(app_id_raw)
        reason = " ".join(args[1:]) if len(args) > 1 else "Application declined by administrator."

        application = await self.db.get_society_application(app_id)
        if not application or application["status"] != "PENDING":
            self.write_output(f"[ERROR] Application ID {app_id} not found or not in PENDING state.")
            return

        await self.db.resolve_society_application(
            app_id=app_id,
            status="REJECTED",
            reviewer_id=self.bot.user.id if self.bot.user else 0,
            reason=reason,
        )
        self.write_output(f"[SUCCESS] Application #{app_id} REJECTED with reason: '{reason}'")

        applicant = self.bot.get_user(application["applicant_id"])
        if applicant:
            try:
                await applicant.send(
                    f"❌ Society application for **{application['name']}** "
                    f"(`{application['society_code']}`) declined.\n"
                    f"**Reason:** {reason}"
                )
            except Exception:
                pass

