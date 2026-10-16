"""
Interactive Console CLI Service for TARClub (Minecraft-style Terminal Backend).

Allows bot hosters and administrators to execute root management commands,
generate single-use society registration tokens, approve/reject applications,
audit registered societies, and broadcast messages directly from the terminal/tmux session.
Features a pinned bottom input bar with live streaming logs in an upper viewport.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
import signal
import sys
import threading
from typing import TYPE_CHECKING, Any

import discord

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
    "token create",
    "token list",
    "token revoke",
    "applications",
    "approve",
    "reject",
    "say",
    "eval",
    "stop",
    "exit",
    "quit",
]


def _console_completer(text: str, state: int) -> str | None:
    options = [c for c in AVAILABLE_COMMANDS if c.startswith(text.lower())]
    if state < len(options):
        return options[state]
    return None


class ConsoleAwareStreamHandler(logging.StreamHandler):
    """
    StreamHandler that routes background logs into a pinned upper scrolling viewport
    above the fixed bottom prompt bar using DECSTBM ANSI margins.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream or sys.stdout
            is_tty = hasattr(stream, "isatty") and stream.isatty()
            if is_tty:
                _, rows = shutil.get_terminal_size()
                if rows > 4:
                    # Save cursor, jump to bottom of scrolling viewport, print log, restore cursor
                    stream.write(f"\033[s\033[{rows - 2};1H\n{msg}\033[u")
                    stream.flush()
                    try:
                        import readline

                        readline.redisplay()
                    except Exception as exc:
                        logger.debug("Readline redisplay skipped: %s", exc)
                    return

            # Fallback for non-TTY or small window
            stream.write(f"\r\033[K{msg}\n" if is_tty else f"{msg}\n")
            stream.flush()
        except Exception:
            self.handleError(record)


class ConsoleService:
    """Provides an interactive CLI prompt pinned to the terminal bottom."""

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

    def _setup_viewport(self) -> None:
        """Sets up ANSI DECSTBM scrolling margins for upper logs with pinned bottom prompt."""
        if sys.stdout is None or not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            return
        cols, rows = shutil.get_terminal_size()
        if rows > 4:
            # Set scroll region: lines 1 to rows - 2
            sys.stdout.write(f"\033[1;{rows - 2}r")
            # Draw cyan divider bar on row rows - 1
            sys.stdout.write(f"\033[{rows - 1};1H\033[2K\033[36m{'─' * cols}\033[0m")
            # Move cursor to bottom row
            sys.stdout.write(f"\033[{rows};1H\033[2K")
            sys.stdout.flush()
            try:
                import readline

                readline.redisplay()
            except Exception as exc:
                logger.debug("Readline redisplay notice: %s", exc)

    def _handle_winch(self, *_: Any) -> None:
        """Re-establishes terminal boundaries when the terminal window is resized."""
        if self.running:
            self._setup_viewport()

    def write_output(self, text: str) -> None:
        """Outputs text into the upper scrolling viewport without displacing bottom prompt."""
        if sys.stdout is None or not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            print(text)
            return

        _, rows = shutil.get_terminal_size()
        if rows > 4 and self.running:
            for line in str(text).splitlines():
                sys.stdout.write(f"\033[s\033[{rows - 2};1H\n{line}\033[u")
            sys.stdout.flush()
            try:
                import readline

                readline.redisplay()
            except Exception as exc:
                logger.debug("Readline redisplay notice: %s", exc)
        else:
            print(text)

    def start(self) -> None:
        """Starts the interactive console thread if running in an interactive terminal."""
        if not getattr(self.settings, "enable_console", True):
            logger.info("[Console] Terminal console disabled via configuration.")
            return

        # Check if stdin is an interactive TTY. If running as systemd daemon or piped, disable CLI.
        if sys.stdin is None or not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
            logger.info("[Console] Non-interactive environment detected. Terminal CLI disabled.")
            return

        # Attach ConsoleAwareStreamHandler to root and tarclub loggers
        try:
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
        except Exception as e:
            logger.debug("Could not attach ConsoleAwareStreamHandler: %s", e)

        # Configure tab-completion and GNU readline
        try:
            import readline

            readline.set_completer(_console_completer)
            readline.parse_and_bind("tab: complete")
        except Exception as exc:
            logger.debug("Readline binding skipped: %s", exc)

        # Register terminal resize handler
        try:
            signal.signal(signal.SIGWINCH, self._handle_winch)
        except Exception as exc:
            logger.debug("SIGWINCH handler skipped: %s", exc)

        self.running = True
        self._loop = asyncio.get_running_loop()
        self._setup_viewport()

        self._thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name="tarclub_console_reader",
        )
        self._thread.start()
        self.write_output(
            "🎮 TARClub Console initialized (Bottom input bar pinned). Type 'help' for commands."
        )

    def stop(self) -> None:
        """Flags the console thread to stop and restores full terminal scroll margins."""
        self.running = False
        if sys.stdout is not None and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            _, rows = shutil.get_terminal_size()
            sys.stdout.write(f"\033[r\033[{rows};1H\033[2K\n")
            sys.stdout.flush()

    def _reader_loop(self) -> None:
        """Runs in a daemon thread, reading stdin line by line with GNU readline support."""
        while self.running:
            try:
                # Ensure cursor is docked on bottom row
                if sys.stdout.isatty():
                    _, rows = shutil.get_terminal_size()
                    if rows > 4:
                        sys.stdout.write(f"\033[{rows};1H")
                        sys.stdout.flush()

                line = input(self.prompt)
                cleaned = line.strip()
                if not cleaned:
                    continue

                # Echo command into upper log viewport
                self.write_output(f"\033[33m[CMD] > {cleaned}\033[0m")

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
        help_text = """
======================= TARClub Console Commands =======================
  help, ?                               Show this command reference
  status                                View bot latency, connected guilds, societies
  guilds                                List connected Discord servers
  societies                             List all registered societies and visibility
  privacy <guild_id> <true|false>       Toggle public directory visibility
  token create <code> <name> [fac] [p]  Generate single-use society registration token
  token list [active|used|revoked]      List registration tokens
  token revoke <token>                  Revoke an unused registration token
  applications                          List pending society registration requests
  approve <app_id>                      Approve application & provision server
  reject <app_id> [reason]              Reject application with reason
  say <guild_id> <chan_id> <msg>        Send message to a channel as TARClub
  eval <code>                           Execute Python expression/statement
  stop, exit, quit                      Gracefully shut down TARClub
========================================================================
"""
        self.write_output(help_text.strip())

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
