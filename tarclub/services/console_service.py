"""
Interactive Console CLI Service for TARClub (Minecraft-style Terminal Backend).

Allows bot hosters and administrators to execute root management commands,
generate single-use society registration tokens, approve/reject applications,
audit registered societies, and broadcast messages directly from the terminal/tmux
session with full scrollback support.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import CompleteStyle

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
    "  clear                                 Clear terminal screen",
    "  stop, exit, quit                      Gracefully shut down TARClub",
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


class ConsoleAwareStreamHandler(logging.StreamHandler):
    """
    StreamHandler that writes background logs cleanly above the interactive prompt,
    compatible with prompt_toolkit and preserving full terminal/tmux scrollback.
    """

    _prompt_active: bool = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Audit log records for user console CMD input are saved to file logs,
            # but not echoed back to console stdout since the user already typed them.
            if record.name == "tarclub.console" and record.getMessage().startswith("[CMD]"):
                return

            msg = self.format(record)
            sys.stdout.write(f"{msg}\n")
            sys.stdout.flush()
        except Exception:
            self.handleError(record)


class ConsoleService:
    """Provides an interactive CLI prompt for TARClub without disrupting terminal scrollback."""

    def __init__(
        self,
        bot: TARClubBot,
        db: Database,
        settings: Settings,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.prompt = "tarclub> "

    def write_output(self, text: str) -> None:
        """Outputs text cleanly without redisplaying prompt mid-command."""
        stream = sys.stdout
        if stream is None or not hasattr(stream, "isatty") or not stream.isatty():
            print(text)
            return

        for line in str(text).splitlines():
            stream.write(f"{line}\n")
        stream.flush()

    def start(self) -> None:
        """Starts the interactive console thread if running in an interactive terminal."""
        if not getattr(self.settings, "enable_console", True):
            logger.info("[Console] Terminal console disabled via configuration.")
            return

        # Check if stdin is an interactive TTY. If running as systemd daemon or piped, disable CLI.
        if sys.stdin is None or not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
            logger.info("[Console] Non-interactive environment detected. Terminal CLI disabled.")
            return

        # Reset any lingering scroll margins from prior runs
        if sys.stdout is not None and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            sys.stdout.write("\033[r")
            sys.stdout.flush()

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
            has_file_handler = any(
                isinstance(h, RotatingFileHandler) for h in root_logger.handlers
            )
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
            logger.debug("Could not attach ConsoleAwareStreamHandler: %s", e)

        self.running = True
        self._loop = asyncio.get_running_loop()

        self._thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name="tarclub_console_reader",
        )
        self._thread.start()
        self.write_output(
            "🎮 TARClub Console initialized. Type 'help' for commands | 'exit' to quit."
        )

    def stop(self) -> None:
        """Flags the console thread to stop and restores normal terminal state."""
        self.running = False
        ConsoleAwareStreamHandler._prompt_active = False
        if sys.stdout is not None and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            sys.stdout.write("\033[r\n")
            sys.stdout.flush()

    def _show_escable_help(self) -> None:
        """Displays help menu in an alternate screen buffer dismissable with ESC, 'q', or Enter."""
        if not (
            sys.stdin
            and hasattr(sys.stdin, "isatty")
            and sys.stdin.isatty()
            and sys.stdout
            and hasattr(sys.stdout, "isatty")
            and sys.stdout.isatty()
        ):
            self._cmd_help()
            return

        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            # Enter alternate screen buffer, clear screen, hide cursor
            sys.stdout.write("\033[?1049h\033[H\033[2J\033[?25l")
            content = "\n".join(HELP_LINES)
            sys.stdout.write(
                content + "\n\n  \033[90m[ Press ESC, 'q', or Enter to return to console ]\033[0m\n"
            )
            sys.stdout.flush()

            tty.setcbreak(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\x1b", "q", "Q", "\r", "\n", " "):
                    break
        except Exception as exc:
            logger.debug("Escable help notice: %s", exc)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception as exc:
                logger.debug("Terminal settings restore skipped: %s", exc)
            # Show cursor, restore primary screen buffer
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()

    def _reader_loop(self) -> None:
        """Runs in a daemon thread, reading stdin line by line with prompt_toolkit support."""
        history_file = Path("logs/.tarclub_history")
        history_file.parent.mkdir(parents=True, exist_ok=True)

        completer = NestedCompleter.from_nested_dict({
            "help": None,
            "?": None,
            "status": None,
            "guilds": None,
            "societies": None,
            "privacy": {"true": None, "false": None},
            "token": {
                "create": None,
                "list": {"active": None, "used": None, "revoked": None},
                "revoke": None,
                "prune": None,
            },
            "applications": {"pending": None, "approved": None, "rejected": None},
            "approve": None,
            "reject": None,
            "backup": {"snapshot": None, "manual": None},
            "say": None,
            "eval": None,
            "clear": None,
            "stop": None,
            "exit": None,
            "quit": None,
        })

        session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_file)),
            completer=completer,
            complete_style=CompleteStyle.READLINE_LIKE,
        )

        with patch_stdout(raw=True):
            while self.running:
                try:
                    ConsoleAwareStreamHandler._prompt_active = True
                    try:
                        line = session.prompt(ANSI("\033[1;36mtarclub>\033[0m "))
                    finally:
                        ConsoleAwareStreamHandler._prompt_active = False

                    cleaned = line.strip()
                    if not cleaned:
                        continue

                    # Record user CMD input with timestamp into bot file logs
                    logger.info("[CMD] Executed: %s", cleaned)

                    # Escable help menu: rendered in alternate screen buffer
                    # so it never clutters logs or scrollback
                    if cleaned.lower() in ("help", "?"):
                        self._show_escable_help()
                        continue

                    if self._loop and self._loop.is_running():
                        future = asyncio.run_coroutine_threadsafe(
                            self.handle_command(cleaned), self._loop
                        )
                        try:
                            future.result(timeout=30)
                        except Exception as e:
                            self.write_output(f"[ERROR] Command failed: {e}")
                    else:
                        break
                except (EOFError, KeyboardInterrupt):
                    logger.info("[Console] EOF/Interrupt received. Terminal CLI stopped.")
                    break
                except Exception as e:
                    logger.debug("Error reading stdin: %s", e)
                    break

    async def handle_command(self, raw_line: str) -> None:
        """Parses and executes a console command line."""
        try:
            parts = shlex.split(raw_line)
        except ValueError as exc:
            self.write_output(f"[ERROR] Syntax error parsing command line: {exc}")
            return

        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        try:
            if cmd in ("help", "?"):
                self._cmd_help()
            elif cmd == "status":
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
            elif cmd == "say":
                await self._cmd_say(args)
            elif cmd == "eval":
                await self._cmd_eval(args)
            elif cmd == "clear":
                self._cmd_clear()
            elif cmd in ("stop", "exit", "quit"):
                await self._cmd_stop()
            else:
                self.write_output(f"[WARN] Unknown command '{cmd}'. Type 'help' for commands.")
        except Exception as e:
            self.write_output(f"[ERROR] Unhandled exception during command '{cmd}': {e}")
            logger.exception("Console command error")

    # ==========================================
    # Command Implementations
    # ==========================================

    def _cmd_help(self) -> None:
        self.write_output("\n".join(HELP_LINES))

    def _cmd_clear(self) -> None:
        if sys.stdout is not None and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()

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

    async def _cmd_say(self, args: list[str]) -> None:
        if len(args) < 3:
            self.write_output("[WARN] Usage: say <guild_id> <channel_id> <message>")
            return

        g_id_raw, c_id_raw = args[0], args[1]
        if not g_id_raw.isdigit() or not c_id_raw.isdigit():
            self.write_output("[ERROR] guild_id and channel_id must be integers.")
            return

        guild = self.bot.get_guild(int(g_id_raw))
        if not guild:
            self.write_output(f"[ERROR] Bot is not in guild ID {g_id_raw}.")
            return

        channel = guild.get_channel(int(c_id_raw))
        if not channel or not isinstance(channel, discord.TextChannel):
            self.write_output(f"[ERROR] TextChannel {c_id_raw} not found in guild {guild.name}.")
            return

        msg = " ".join(args[2:])
        try:
            await channel.send(msg)
            self.write_output(f"[SUCCESS] Message sent to #{channel.name} in {guild.name}.")
        except Exception as e:
            self.write_output(f"[ERROR] Failed to send message: {e}")

    async def _cmd_eval(self, args: list[str]) -> None:
        if not args:
            self.write_output("[WARN] Usage: eval <expression>")
            return

        code = " ".join(args)
        scope = {
            "bot": self.bot,
            "db": self.db,
            "settings": self.settings,
            "discord": discord,
            "asyncio": asyncio,
        }
        try:
            result = eval(code, scope)
            if asyncio.iscoroutine(result):
                result = await result
            self.write_output(f"[EVAL] => {result!r}")
        except Exception as e:
            self.write_output(f"[EVAL ERROR] {type(e).__name__}: {e}")

    async def _cmd_stop(self) -> None:
        self.write_output("[INFO] Shutting down bot gracefully from console...")
        self.stop()
        await self.bot.close()
