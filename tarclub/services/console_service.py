"""
Interactive Console CLI Service for TARClub (Minecraft-style Terminal Backend).

Allows bot hosters and administrators to execute root management commands,
generate single-use society registration tokens, approve/reject applications,
audit registered societies, and broadcast messages directly from the terminal/tmux
session with full scrollback support.
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import shlex
import socket
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from prompt_toolkit.application import Application
from prompt_toolkit.completion import DynamicCompleter, NestedCompleter
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import Float, FloatContainer, HSplit
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

try:
    import pam
except ImportError:
    pam = None

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


class ConsoleAwareStreamHandler(logging.StreamHandler):
    """
    StreamHandler that buffers startup logs and routes real-time logs to the
    interactive Split-Screen TUI console or standard stdout.
    """

    _prompt_active: bool = False
    _log_buffer: list[str] = []
    _console_instance: Any = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Audit log records for user console CMD input are saved to file logs,
            # but not echoed back to console stdout since the user already typed them.
            if record.name == "tarclub.console" and record.getMessage().startswith("[CMD]"):
                return

            msg = self.format(record)
            ConsoleAwareStreamHandler._log_buffer.append(msg)
            if len(ConsoleAwareStreamHandler._log_buffer) > 1000:
                ConsoleAwareStreamHandler._log_buffer.pop(0)

            if ConsoleAwareStreamHandler._console_instance and getattr(
                ConsoleAwareStreamHandler._console_instance, "is_active", False
            ):
                ConsoleAwareStreamHandler._console_instance.append_log(msg)
            else:
                sys.stdout.write(f"{msg}\n")
                sys.stdout.flush()
        except Exception:
            self.handleError(record)


def is_linux_admin(username: str) -> bool:
    """Check if a username is an administrator (root or sudo/wheel) on the Linux system."""
    try:
        import grp
        import pwd

        pw = pwd.getpwnam(username)
        if pw.pw_uid == 0:
            return True

        admin_groups = {"sudo", "wheel", "admin", "root"}
        user_groups = set()
        for g in grp.getgrall():
            if username in g.gr_mem or g.gr_gid == pw.pw_gid:
                user_groups.add(g.gr_name)

        if user_groups & admin_groups:
            return True

        if username == getpass.getuser():
            return True

        return False
    except (KeyError, ImportError, Exception):
        return False


class ConsoleService:
    """
    Provides an interactive Split-Screen TUI for TARClub with upper logs
    and compact bottom prompt.
    """

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
        self.is_active = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self.app: Application[None] | None = None
        self.log_area: TextArea | None = None
        self.output_area: TextArea | None = None
        self.input_box: TextArea | None = None

        # Linux PAM Authentication & Console Security State
        self.is_authenticated: bool = False
        self.authenticated_user: str | None = None
        self._is_password_mode: bool = False
        self._pending_auth_user: str | None = None
        self._pending_auth_action: str = "login"
        self._failed_attempts: int = 0
        self._locked_until: float = 0.0

    def _get_prompt(self) -> str:
        if self._is_password_mode:
            return f"Password for {self._pending_auth_user}: "
        if self.is_authenticated:
            return f"tarclub [{self.authenticated_user}]> "
        return "tarclub [LOCKED]> "

    def _get_completer(self) -> NestedCompleter | None:
        if self._is_password_mode:
            return None
        if not self.is_authenticated:
            return NestedCompleter.from_nested_dict(
                {
                    "help": None,
                    "?": None,
                    "login": None,
                    "setup-admin": None,
                    "clear": {"output": None, "logs": None, "all": None},
                    "stop": None,
                    "exit": None,
                    "quit": None,
                }
            )
        return NestedCompleter.from_nested_dict(
            {
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
                "login": None,
                "setup-admin": None,
                "lock": None,
                "logout": None,
                "user": {"list": None, "add": None, "remove": None},
                "clear": {"output": None, "logs": None, "all": None},
                "stop": None,
                "exit": None,
                "quit": None,
            }
        )

    def append_log(self, text: str) -> None:
        """Appends log text to the top pane and scrolls to bottom."""
        if not self.is_active or not self.log_area or not self.app:
            return

        def _update() -> None:
            if self.log_area and self.app:
                self.log_area.text += f"{text}\n"
                self.log_area.buffer.cursor_position = len(self.log_area.text)
                self.app.invalidate()

        if self._loop and self._loop.is_running():
            try:
                curr = asyncio.get_running_loop()
                if curr is self._loop:
                    _update()
                else:
                    self._loop.call_soon_threadsafe(_update)
            except RuntimeError:
                self._loop.call_soon_threadsafe(_update)

    def append_output(self, text: str) -> None:
        """Appends output text to the command output pane and scrolls to bottom."""
        if not self.is_active or not self.output_area or not self.app:
            return

        def _update() -> None:
            if self.output_area and self.app:
                self.output_area.text += f"{text}\n"
                self.output_area.buffer.cursor_position = len(self.output_area.text)
                self.app.invalidate()

        if self._loop and self._loop.is_running():
            try:
                curr = asyncio.get_running_loop()
                if curr is self._loop:
                    _update()
                else:
                    self._loop.call_soon_threadsafe(_update)
            except RuntimeError:
                self._loop.call_soon_threadsafe(_update)

    def write_output(self, text: str) -> None:
        """Outputs text cleanly to output_area if TUI is active, else sys.stdout."""
        if self.is_active:
            for line in str(text).splitlines():
                self.append_output(line)
        else:
            for line in str(text).splitlines():
                sys.stdout.write(f"{line}\n")
            sys.stdout.flush()

    def start(self) -> None:
        """Starts the interactive Split-Screen TUI console if running in an interactive terminal."""
        if not getattr(self.settings, "enable_console", True):
            logger.info("[Console] Terminal console disabled via configuration.")
            return

        # Check if stdin is an interactive TTY. If running as systemd daemon or piped, disable CLI.
        if sys.stdin is None or not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
            logger.info("[Console] Non-interactive environment detected. Terminal CLI disabled.")
            return

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
            logger.debug("Could not attach ConsoleAwareStreamHandler: %s", e)

        self.running = True
        self.is_active = True
        self._loop = asyncio.get_running_loop()
        ConsoleAwareStreamHandler._console_instance = self

        history_file = Path("logs/.tarclub_history")
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history = FileHistory(str(history_file))

        initial_logs = "\n".join(ConsoleAwareStreamHandler._log_buffer)
        if initial_logs:
            initial_logs += "\n"

        self.log_area = TextArea(
            text=initial_logs,
            read_only=True,
            scrollbar=True,
            wrap_lines=True,
            focusable=False,
        )
        self.log_area.buffer.cursor_position = len(self.log_area.text)

        initial_output = (
            "🎮 TARClub Console initialized.\n"
            "🔒 Console is locked. Type 'login <username>' to authenticate "
            "(or 'setup-admin <username>' if first time).\n"
        )
        self.output_area = TextArea(
            text=initial_output,
            read_only=True,
            scrollbar=True,
            wrap_lines=True,
            focusable=False,
        )
        self.output_area.buffer.cursor_position = len(self.output_area.text)

        style = Style.from_dict(
            {
                "text-area.prompt": "ansicyan bold",
                "frame.label": "ansigreen bold",
                "frame.border": "#45475a",
                "completion-menu": "bg:#1e1e2e #cdd6f4",
                "completion-menu.completion": "bg:#1e1e2e #cdd6f4",
                "completion-menu.completion.current": "bg:#45475a #89b4fa bold",
                "scrollbar.background": "bg:#11111b",
                "scrollbar.button": "bg:#585b70",
            }
        )

        self.input_box = TextArea(
            height=1,
            prompt=lambda: self._get_prompt(),
            password=Condition(lambda: self._is_password_mode),
            multiline=False,
            wrap_lines=False,
            history=history,
            completer=DynamicCompleter(self._get_completer),
            accept_handler=self._on_accept,
        )

        kb = KeyBindings()

        @kb.add("pageup")
        def _scroll_up(event):
            if self.output_area:
                self.output_area.buffer.cursor_up(count=10)

        @kb.add("pagedown")
        def _scroll_down(event):
            if self.output_area:
                self.output_area.buffer.cursor_down(count=10)

        @kb.add("c-up")
        def _scroll_logs_up(event):
            if self.log_area:
                self.log_area.buffer.cursor_up(count=10)

        @kb.add("c-down")
        def _scroll_logs_down(event):
            if self.log_area:
                self.log_area.buffer.cursor_down(count=10)

        @kb.add("s-pageup")
        def _scroll_logs_sup(event):
            if self.log_area:
                self.log_area.buffer.cursor_up(count=10)

        @kb.add("s-pagedown")
        def _scroll_logs_sdown(event):
            if self.log_area:
                self.log_area.buffer.cursor_down(count=10)

        @kb.add("tab")
        def _on_tab(event):
            b = self.input_box.buffer if self.input_box else event.app.current_buffer
            if b.complete_state:
                b.complete_next()
            else:
                b.start_completion(select_first=False)

        @kb.add("s-tab")
        def _on_s_tab(event):
            b = self.input_box.buffer if self.input_box else event.app.current_buffer
            if b.complete_state:
                b.complete_previous()

        @kb.add("down", filter=has_completions)
        def _on_down(event):
            b = self.input_box.buffer if self.input_box else event.app.current_buffer
            b.complete_next()

        @kb.add("up", filter=has_completions)
        def _on_up(event):
            b = self.input_box.buffer if self.input_box else event.app.current_buffer
            b.complete_previous()

        @kb.add("escape", filter=has_completions)
        def _on_escape(event):
            b = self.input_box.buffer if self.input_box else event.app.current_buffer
            b.cancel_completion()

        last_ctrl_c = 0.0

        @kb.add("c-c")
        def _on_ctrl_c(event):
            nonlocal last_ctrl_c
            if self._is_password_mode:
                self._is_password_mode = False
                self._pending_auth_user = None
                self.write_output("[AUTH CANCELLED] Password entry cancelled.")
                if self.app:
                    self.app.invalidate()
                return

            now = time.monotonic()
            if (not self.input_box or not self.input_box.text) or (now - last_ctrl_c < 2.0):
                self.write_output("[INFO] Ctrl+C received. Shutting down bot gracefully...")
                event.app.exit()
                if self._loop and self._loop.is_running():
                    asyncio.create_task(self._cmd_stop())
                return

            last_ctrl_c = now
            if self.input_box:
                self.input_box.text = ""
            self.write_output(
                "[CONSOLE] Press Ctrl+C again (or type 'stop' / 'exit') to shut down."
            )

        @kb.add("c-d")
        def _on_ctrl_d(event):
            event.app.exit()
            if self._loop and self._loop.is_running():
                asyncio.create_task(self._cmd_stop())

        body = HSplit(
            [
                Frame(self.log_area, title="📡 System Logs (Ctrl+Up/Down to scroll)"),
                Frame(self.output_area, title="💻 Terminal Output (PgUp/PgDn to scroll)"),
                self.input_box,
            ]
        )

        root = FloatContainer(
            content=body,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=16),
                )
            ],
        )

        self.app = Application(
            layout=Layout(root, focused_element=self.input_box),
            key_bindings=kb,
            style=style,
            full_screen=True,
            mouse_support=True,
        )

        self._task = asyncio.create_task(self._run_app())

    async def _run_app(self) -> None:
        try:
            if self.app:
                await self.app.run_async()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Console TUI error: %s", exc)
        finally:
            self.is_active = False
            self.running = False
            ConsoleAwareStreamHandler._console_instance = None

    def _on_accept(self, buff) -> bool:
        text = buff.text
        buff.text = ""

        if self._is_password_mode:
            self._is_password_mode = False
            if not text:
                self.write_output("[AUTH CANCELLED] Password entry cancelled.")
                self._pending_auth_user = None
                if self.app:
                    self.app.invalidate()
                return False
            self.write_output(f"{self._get_prompt()}********")
            if self._loop and self._loop.is_running():
                asyncio.create_task(self._handle_password_input(text))
            return False

        cmd = text.strip()
        if cmd:
            logger.info("[CMD] Executed: %s", cmd)
            self.write_output(f"{self._get_prompt()}{cmd}")
            if self._loop and self._loop.is_running():
                asyncio.create_task(self.handle_command(cmd))
        return False

    def stop(self) -> None:
        """Stops the interactive split-screen TUI console."""
        self.running = False
        self.is_active = False
        ConsoleAwareStreamHandler._console_instance = None
        if self.app and self.app.is_running:
            self.app.exit()
        if self._task and not self._task.done():
            self._task.cancel()

    def _show_escable_help(self) -> None:
        self._cmd_help()

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
            # Commands unconditionally accessible when locked
            if cmd in ("help", "?"):
                self._cmd_help()
                return
            elif cmd == "clear":
                self._cmd_clear(args)
                return
            elif cmd in ("stop", "exit", "quit"):
                await self._cmd_stop()
                return
            elif cmd == "setup-admin":
                await self._cmd_setup_admin(args)
                return
            elif cmd == "login":
                await self._cmd_login(args)
                return

            # Strict Authentication Gate - No bypass allowed
            if not self.is_authenticated:
                self.write_output(
                    "🔒 [AUTH REQUIRED] Console is locked. Run 'login <username>' to authenticate "
                    "(or 'setup-admin <username>' if initializing for the first time)."
                )
                return

            # Authenticated commands
            if cmd in ("lock", "logout"):
                self._cmd_lock()
            elif cmd == "user":
                await self._cmd_user(args)
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
            else:
                self.write_output(f"[WARN] Unknown command '{cmd}'. Type 'help' for commands.")
        except Exception as e:
            self.write_output(f"[ERROR] Unhandled exception during command '{cmd}': {e}")
            logger.exception("Console command error")

    # ==========================================
    # Command Implementations
    # ==========================================

    def _cmd_help(self) -> None:
        status_line = (
            f"  Status: 🔓 AUTHENTICATED ({self.authenticated_user})"
            if self.is_authenticated
            else "  Status: 🔒 LOCKED (Run 'login <username>' to authenticate)"
        )
        banner = [
            HELP_LINES[0],
            status_line,
            "------------------------------------------------------------------------",
        ]
        self.write_output("\n".join(banner + HELP_LINES[1:]))

    def _pam_authenticate(self, username: str, password: str) -> bool:
        if pam is None:
            logger.error("python-pam library not installed; authentication failing.")
            return False
        try:
            p = pam.pam()
            return bool(p.authenticate(username, password, service="login"))
        except Exception as exc:
            logger.warning("PAM authentication exception for %s: %s", username, exc)
            return False

    async def _is_authorized_admin(self, username: str) -> bool:
        """Determines if a user has authority to access the console."""
        # 1. Explicitly authorized in database
        if await self.db.is_console_admin(username):
            return True
        # 2. Linux system administrator (sudo/wheel/root or bot process owner)
        if is_linux_admin(username):
            return True
        return False

    async def _handle_password_input(self, password: str) -> None:
        username = self._pending_auth_user
        action = self._pending_auth_action
        self._pending_auth_user = None
        self._pending_auth_action = "login"

        if not username:
            return

        now = time.monotonic()
        if now < self._locked_until:
            rem = int(self._locked_until - now)
            self.write_output(
                f"🚨 [SECURITY LOCKOUT] Console is temporarily locked due to repeated failed "
                f"attempts. Try again in {rem}s."
            )
            return

        success = await asyncio.to_thread(self._pam_authenticate, username, password)

        if success:
            self._failed_attempts = 0
            self.is_authenticated = True
            self.authenticated_user = username

            if action == "setup_admin":
                await self.db.add_console_admin(username, role="admin", added_by="bootstrap")
                self.write_output(
                    f"🎉 [SECURITY] Root administrator '{username}' successfully initialized!"
                )
                await self._dispatch_security_alert(
                    title="🛡️ Console Admin Initialized",
                    description=(
                        f"First console administrator **{username}** was initialized via bootstrap."
                    ),
                    color=0x2ECC71,
                )
            else:
                if not await self.db.is_console_admin(username):
                    await self.db.add_console_admin(username, role="admin", added_by="linux_pam")

            self.write_output(
                f"🔓 [AUTH SUCCESS] Welcome, {username}! Console unlocked. "
                "Type 'help' for commands."
            )
            if self.app:
                self.app.invalidate()

            await self._dispatch_security_alert(
                title="🔐 Console Login Successful",
                description=(
                    f"Administrator **{username}** authenticated via Linux PAM and unlocked "
                    "backend terminal session."
                ),
                color=0x57F287,
            )
        else:
            self._failed_attempts += 1
            self.write_output(
                f"❌ [AUTH FAILED] Invalid credentials for user '{username}'. "
                f"Attempt {self._failed_attempts}/3."
            )
            if self._failed_attempts >= 3:
                self._locked_until = time.monotonic() + 900  # 15 minutes
                self.write_output(
                    "🚨 [SECURITY LOCKOUT] 3 failed authentication attempts detected. "
                    "Console locked for 15 minutes."
                )
                await self._dispatch_security_alert(
                    title="🚨 Console Brute-Force Lockout",
                    description=(
                        f"**3 failed authentication attempts** detected for user **{username}**.\n"
                        "Console locked for 15 minutes."
                    ),
                    color=0xED4245,
                )

    async def _cmd_setup_admin(self, args: list[str]) -> None:
        username = args[0].strip() if args else getpass.getuser()

        has_admin = await self.db.has_any_console_admin()
        if has_admin:
            self.write_output(
                "🔒 [AUTH ERROR] An administrator account already exists. "
                "Please login with an authorized account or ask an administrator to add you."
            )
            return

        if not is_linux_admin(username):
            self.write_output(
                f"❌ [AUTH ERROR] User '{username}' is not a Linux administrator "
                "(must belong to sudo/wheel group or root)."
            )
            return

        now = time.monotonic()
        if now < self._locked_until:
            rem = int(self._locked_until - now)
            self.write_output(f"🚨 [SECURITY LOCKOUT] Console is locked. Try again in {rem}s.")
            return

        self._pending_auth_user = username
        self._pending_auth_action = "setup_admin"

        if len(args) >= 2:
            await self._handle_password_input(args[1])
            return

        self._is_password_mode = True
        self.write_output(
            f"🔑 Initializing root admin '{username}'. Please enter Linux PAM password:"
        )
        if self.app:
            self.app.invalidate()

    async def _cmd_login(self, args: list[str]) -> None:
        if self.is_authenticated:
            self.write_output(
                f"[INFO] Already logged in as '{self.authenticated_user}'. "
                "Run 'lock' or 'logout' to switch users."
            )
            return

        username = args[0].strip() if args else getpass.getuser()

        now = time.monotonic()
        if now < self._locked_until:
            rem = int(self._locked_until - now)
            self.write_output(f"🚨 [SECURITY LOCKOUT] Console is locked. Try again in {rem}s.")
            return

        has_admin = await self.db.has_any_console_admin()
        if not has_admin:
            self.write_output(
                "🔒 [AUTH REQUIRED] No console administrators have been initialized yet."
            )
            self.write_output(
                f"Run 'setup-admin {username}' to bootstrap this account as root administrator."
            )
            return

        if not await self._is_authorized_admin(username):
            self.write_output(
                f"❌ [AUTH ERROR] User '{username}' is not an authorized console administrator."
            )
            return

        self._pending_auth_user = username
        self._pending_auth_action = "login"

        if len(args) >= 2:
            await self._handle_password_input(args[1])
            return

        self._is_password_mode = True
        self.write_output(f"🔑 Enter Linux PAM password for '{username}':")
        if self.app:
            self.app.invalidate()

    def _cmd_lock(self) -> None:
        if not self.is_authenticated:
            self.write_output("[INFO] Console is already locked.")
            return
        user = self.authenticated_user
        self.is_authenticated = False
        self.authenticated_user = None
        self._is_password_mode = False
        self._pending_auth_user = None
        self.write_output(f"🔒 [CONSOLE] User '{user}' logged out. Console locked.")
        if self.app:
            self.app.invalidate()

    async def _cmd_user(self, args: list[str]) -> None:
        if not args:
            self.write_output("[WARN] Usage: user <list|add|remove> [username]")
            return

        sub = args[0].lower()
        if sub == "list":
            admins = await self.db.get_console_admins()
            if not admins:
                self.write_output("No console administrators registered.")
                return
            self.write_output("================ Authorized Console Admins ================")
            for a in admins:
                added_by = a.get("added_by") or "system"
                added_at = a.get("added_at") or "unknown"
                role = a.get("role", "admin")
                self.write_output(
                    f" • {a['username']} (Role: {role}, Added by: {added_by}, Date: {added_at})"
                )
            self.write_output("===========================================================")
        elif sub == "add":
            if len(args) < 2:
                self.write_output("[WARN] Usage: user add <username>")
                return
            target = args[1].strip()
            # Verify target exists as a valid Linux user
            try:
                import pwd

                pwd.getpwnam(target)
            except (KeyError, ImportError):
                self.write_output(
                    f"❌ [ERROR] User '{target}' does not exist on this Linux system."
                )
                return

            if await self.db.is_console_admin(target):
                self.write_output(
                    f"[WARN] User '{target}' is already an authorized console administrator."
                )
                return
            await self.db.add_console_admin(target, role="admin", added_by=self.authenticated_user)
            self.write_output(f"✅ Added '{target}' as authorized console administrator.")
            await self._dispatch_security_alert(
                title="🛡️ Console Admin Added",
                description=(
                    f"Administrator **{self.authenticated_user}** added **{target}** as console "
                    "admin."
                ),
                color=0x3498DB,
            )
        elif sub in ("remove", "delete", "del"):
            if len(args) < 2:
                self.write_output("[WARN] Usage: user remove <username>")
                return
            target = args[1].strip()
            admins = await self.db.get_console_admins()
            if len(admins) <= 1 and any(a["username"] == target for a in admins):
                self.write_output(
                    f"❌ Cannot remove '{target}': At least one console administrator must remain."
                )
                return
            removed = await self.db.remove_console_admin(target)
            if removed:
                self.write_output(f"✅ Removed '{target}' from console administrators.")
                await self._dispatch_security_alert(
                    title="🛡️ Console Admin Removed",
                    description=(
                        f"Administrator **{self.authenticated_user}** removed **{target}** from "
                        "console admins."
                    ),
                    color=0xE67E22,
                )
            else:
                self.write_output(
                    f"[WARN] User '{target}' was not found in console administrators."
                )
        else:
            self.write_output(
                f"[WARN] Unknown subcommand 'user {sub}'. Available: list, add, remove"
            )

    async def _dispatch_security_alert(self, title: str, description: str, color: int) -> None:
        try:
            embed = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=discord.utils.utcnow(),
            )
            host_info = f"{getpass.getuser()}@{socket.gethostname()}"
            embed.add_field(name="Host", value=f"`{host_info}`", inline=True)
            bot_tag = str(self.bot.user) if self.bot.user else "TARClub"
            embed.set_footer(text=f"TARClub Console Security • {bot_tag}")

            # Send to review_channel_id if configured
            rev_chan_id = getattr(self.settings, "review_channel_id", None)
            if rev_chan_id:
                chan = self.bot.get_channel(rev_chan_id)
                if not chan:
                    try:
                        chan = await self.bot.fetch_channel(rev_chan_id)
                    except Exception as exc:
                        logger.debug("Failed fetching review channel: %s", exc)
                if chan and isinstance(chan, discord.TextChannel):
                    try:
                        await chan.send(embed=embed)
                    except Exception as exc:
                        logger.debug("Could not send alert to review channel: %s", exc)

            # Send to connected guilds' system channels
            for guild in self.bot.guilds:
                chan = guild.system_channel
                if chan and isinstance(chan, discord.TextChannel):
                    perms = chan.permissions_for(guild.me)
                    if perms.send_messages and perms.embed_links:
                        try:
                            await chan.send(embed=embed)
                        except Exception as exc:
                            logger.debug("Failed sending security alert to %s: %s", chan.id, exc)
        except Exception as exc:
            logger.debug("Security alert dispatch error: %s", exc)

    def _cmd_clear(self, args: list[str] | None = None) -> None:
        target = args[0].lower() if args else "output"
        if self.is_active:
            if target in ("all", "both"):
                if self.log_area:
                    self.log_area.text = ""
                if self.output_area:
                    self.output_area.text = ""
            elif target in ("logs", "log"):
                if self.log_area:
                    self.log_area.text = ""
            else:
                if self.output_area:
                    self.output_area.text = ""
            if self.app:
                self.app.invalidate()
        elif sys.stdout is not None and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
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
        try:
            await asyncio.wait_for(self.bot.close(), timeout=5.0)
        except Exception as e:
            logger.warning("Error during bot shutdown: %s", e)
        finally:
            if self._loop and self._loop.is_running() and "pytest" not in sys.modules:
                self._loop.call_soon(sys.exit, 0)
