"""
Interactive Console CLI Service for TARClub (Minecraft-style Terminal Backend).

Allows bot hosters and administrators to execute root management commands,
generate single-use society registration tokens, approve/reject applications,
audit registered societies, and broadcast messages directly from the terminal/tmux session.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
import threading
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from tarclub.bot import TARClubBot
    from tarclub.config import Settings
    from tarclub.database import Database

logger = logging.getLogger("tarclub.console")


class ConsoleService:
    """Provides an interactive CLI prompt on stdin for TARClub root administration."""

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

    def start(self) -> None:
        """Starts the interactive console thread if running in an interactive terminal."""
        if not getattr(self.settings, "enable_console", True):
            logger.info("[Console] Terminal console disabled via configuration.")
            return

        # Check if stdin is an interactive TTY. If running as systemd daemon or piped, disable CLI.
        if sys.stdin is None or not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
            logger.info("[Console] Non-interactive environment detected. Terminal CLI disabled.")
            return

        self.running = True
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name="tarclub_console_reader",
        )
        self._thread.start()
        print("\n" + "=" * 60)
        print("🎮 TARClub Minecraft-style Console CLI initialized!")
        print("Type 'help' or '?' for available console commands.")
        print("=" * 60 + "\n")
        self._print_prompt()

    def stop(self) -> None:
        """Flags the console thread to stop."""
        self.running = False

    def _print_prompt(self) -> None:
        try:
            sys.stdout.write(f"\r{self.prompt}")
            sys.stdout.flush()
        except Exception:
            pass

    def _reader_loop(self) -> None:
        """Runs in a daemon thread, reading stdin line by line."""
        while self.running:
            try:
                line = sys.stdin.readline()
                if not line:
                    logger.info("[Console] EOF received on stdin. Terminal CLI stopped.")
                    break

                cleaned = line.strip()
                if not cleaned:
                    self._print_prompt()
                    continue

                if self._loop and self._loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self.handle_command(cleaned), self._loop
                    )
                    try:
                        future.result(timeout=30)
                    except Exception as e:
                        print(f"\n[ERROR] Command failed: {e}")
                else:
                    break

                self._print_prompt()
            except Exception as e:
                logger.debug(f"[Console] Error reading stdin: {e}")
                break

    async def handle_command(self, raw_line: str) -> None:
        """Parses and executes a console command line."""
        try:
            parts = shlex.split(raw_line)
        except ValueError as exc:
            print(f"[ERROR] Syntax error parsing command line: {exc}")
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
                print(f"[WARN] Unknown command '{cmd}'. Type 'help' for available commands.")
        except Exception as e:
            print(f"[ERROR] Unhandled exception during command '{cmd}': {e}")
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
        print(help_text.strip())

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

        print(
            f"[STATUS] Bot User: {self.bot.user} "
            f"(ID: {self.bot.user.id if self.bot.user else 'N/A'})"
        )
        print(f"[STATUS] WebSocket Ping: {latency_ms} ms | Guilds: {guilds_count}")
        print(f"[STATUS] Registered Societies: {soc_count} | Active Terms: {active_terms}")
        print(f"[STATUS] Active Tokens: {active_tokens} | Pending Applications: {pending_apps}")
        print(f"[STATUS] Database: {self.settings.database_path}")

    async def _cmd_guilds(self) -> None:
        if not self.bot.guilds:
            print("[INFO] No connected guilds.")
            return

        print(f"\n--- Connected Guilds ({len(self.bot.guilds)}) ---")
        for g in self.bot.guilds:
            soc = await self.db.get_society(g.id)
            soc_tag = f"[{soc['society_code']}] {soc['name']}" if soc else "(Not Registered)"
            print(f"• [{g.id}] {g.name:<30} | Members: {g.member_count:<5} | Society: {soc_tag}")
        print("------------------------------------------------")

    async def _cmd_societies(self) -> None:
        cursor = await self.db.conn.execute("SELECT * FROM societies ORDER BY id ASC;")
        rows = await cursor.fetchall()
        if not rows:
            print("[INFO] No societies registered yet.")
            return

        print(f"\n--- Registered Societies ({len(rows)}) ---")
        for r in rows:
            soc = dict(r)
            vis = "🔒 Private" if soc.get("is_private") else "🌐 Public "
            print(
                f"• ID: {soc['id']:<2} | Code: {soc['society_code']:<12} | "
                f"Faculty: {soc['faculty']:<6} | {vis} | Guild: {soc['guild_id']}\n"
                f"  Name: {soc['name']}"
            )
        print("------------------------------------------")

    async def _cmd_privacy(self, args: list[str]) -> None:
        if len(args) < 2:
            print("[WARN] Usage: privacy <guild_id> <true|false>")
            return

        g_id_raw, val_raw = args[0], args[1].lower()
        if not g_id_raw.isdigit():
            print("[ERROR] guild_id must be numeric.")
            return

        g_id = int(g_id_raw)
        is_private = val_raw in ("true", "1", "yes", "private")

        success = await self.db.set_society_privacy(g_id, is_private)
        if success:
            vis_text = (
                "Private (Hidden from directory)" if is_private else "Public (Listed in directory)"
            )
            print(f"[SUCCESS] Guild {g_id} privacy updated to: {vis_text}")
        else:
            print(f"[WARN] No society found registered under Guild ID {g_id}.")

    async def _cmd_token(self, args: list[str]) -> None:
        if not args:
            print("[WARN] Usage: token <create|list|revoke> [args...]")
            return

        sub = args[0].lower()
        if sub == "create":
            if len(args) < 3:
                print("[WARN] Usage: token create <code> <name> [faculty] [is_private]")
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
            print("\n========================================================")
            print(f"🎉 Generated Registration Token: {token}")
            print(f"• Code:       {code}")
            print(f"• Name:       {name}")
            print(f"• Faculty:    {faculty}")
            print(f"• Visibility: {'Private' if is_private else 'Public'}")
            print("• Instruction: Give this token to the club president.")
            print(f"  They run: `/society register token:{token}` in their server!")
            print("========================================================\n")

        elif sub == "list":
            status_filter = args[1].upper() if len(args) > 1 else None
            tokens = await self.db.list_society_tokens(status=status_filter)
            if not tokens:
                print(f"[INFO] No tokens found matching status: {status_filter or 'ALL'}")
                return

            print(f"\n--- Society Registration Tokens ({len(tokens)}) ---")
            for t in tokens:
                status_badge = f"[{t['status']}]"
                used_by = (
                    f"Guild {t['used_by_guild_id']}" if t.get("used_by_guild_id") else "Unused"
                )
                print(
                    f"• {t['token']} {status_badge:<9} | Code: {t['society_code']:<12} | "
                    f"{t['name']:<25} | {used_by}"
                )
            print("-----------------------------------------------------")

        elif sub == "revoke":
            if len(args) < 2:
                print("[WARN] Usage: token revoke <token>")
                return
            tok = args[1].strip().upper()
            success = await self.db.revoke_society_token(tok)
            if success:
                print(f"[SUCCESS] Token {tok} has been revoked.")
            else:
                print(f"[WARN] Token {tok} not found or was not in ACTIVE status.")
        else:
            print(f"[WARN] Unknown token subcommand '{sub}'. Use create, list, or revoke.")

    async def _cmd_applications(self) -> None:
        apps = await self.db.list_society_applications(status="PENDING")
        if not apps:
            print("[INFO] No pending society applications.")
            return

        print(f"\n--- Pending Society Applications ({len(apps)}) ---")
        for a in apps:
            vis = "Private" if a.get("is_private") else "Public"
            print(
                f"• [ID: {a['id']}] Code: {a['society_code']} | Name: {a['name']} | "
                f"Faculty: {a['faculty']} | {vis}\n"
                f"  Requester: {a['applicant_name']} (User ID: {a['applicant_id']}) | "
                f"Guild: {a['guild_name']} (ID: {a['guild_id']})\n"
                f"  To approve: approve {a['id']}\n"
                f"  To reject:  reject {a['id']} [reason]"
            )
        print("---------------------------------------------------")

    async def _cmd_approve(self, args: list[str]) -> None:
        if not args:
            print("[WARN] Usage: approve <application_id>")
            return

        app_id_raw = args[0]
        if not app_id_raw.isdigit():
            print("[ERROR] application_id must be numeric.")
            return

        app_id = int(app_id_raw)
        application = await self.db.get_society_application(app_id)
        if not application:
            print(f"[ERROR] Application ID {app_id} not found.")
            return

        if application["status"] != "PENDING":
            print(f"[WARN] Application ID {app_id} is already {application['status']}.")
            return

        guild_id = application["guild_id"]
        existing = await self.db.get_society(guild_id)
        if existing:
            print(f"[ERROR] Guild {guild_id} is already registered as {existing['name']}.")
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
            print(
                f"[SUCCESS] Application #{app_id} APPROVED! "
                f"Society ID {soc_id} provisioned for Guild {guild_id}."
            )

            # Try to notify the guild's system channel
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
            print(f"[ERROR] Failed to approve application: {e}")

    async def _cmd_reject(self, args: list[str]) -> None:
        if not args:
            print("[WARN] Usage: reject <application_id> [reason]")
            return

        app_id_raw = args[0]
        if not app_id_raw.isdigit():
            print("[ERROR] application_id must be numeric.")
            return

        app_id = int(app_id_raw)
        reason = " ".join(args[1:]) if len(args) > 1 else "Application declined by administrator."

        application = await self.db.get_society_application(app_id)
        if not application or application["status"] != "PENDING":
            print(f"[ERROR] Application ID {app_id} not found or not in PENDING state.")
            return

        await self.db.resolve_society_application(
            app_id=app_id,
            status="REJECTED",
            reviewer_id=self.bot.user.id if self.bot.user else 0,
            reason=reason,
        )
        print(f"[SUCCESS] Application #{app_id} REJECTED with reason: '{reason}'")

        # Try to notify applicant
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
            print("[WARN] Usage: say <guild_id> <channel_id> <message>")
            return

        g_id_raw, c_id_raw = args[0], args[1]
        if not g_id_raw.isdigit() or not c_id_raw.isdigit():
            print("[ERROR] guild_id and channel_id must be integers.")
            return

        guild = self.bot.get_guild(int(g_id_raw))
        if not guild:
            print(f"[ERROR] Bot is not in guild ID {g_id_raw}.")
            return

        channel = guild.get_channel(int(c_id_raw))
        if not channel or not isinstance(channel, discord.TextChannel):
            print(f"[ERROR] TextChannel {c_id_raw} not found in guild {guild.name}.")
            return

        msg = " ".join(args[2:])
        try:
            await channel.send(msg)
            print(f"[SUCCESS] Message sent to #{channel.name} in {guild.name}.")
        except Exception as e:
            print(f"[ERROR] Failed to send message: {e}")

    async def _cmd_eval(self, args: list[str]) -> None:
        if not args:
            print("[WARN] Usage: eval <expression>")
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
            print(f"[EVAL] => {result!r}")
        except Exception as e:
            print(f"[EVAL ERROR] {type(e).__name__}: {e}")

    async def _cmd_stop(self) -> None:
        print("[INFO] Shutting down bot gracefully from console...")
        self.stop()
        await self.bot.close()
