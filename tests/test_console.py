"""
Unit tests for TARClub Interactive Console CLI, One-Time Tokens, and Application Review System.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tarclub.config import Settings
from tarclub.database import Database
from tarclub.services.console_service import ConsoleService


@pytest.fixture
async def db(tmp_path: Path):
    db_file = tmp_path / "test_console.db"
    database = Database(db_file)
    await database.connect()
    await database.init_tables()
    yield database
    await database.close()


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 999888777666
    bot.user.__str__.return_value = "TARClub#0001"
    bot.latency = 0.035
    bot.guilds = []
    bot.close = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_tokens_lifecycle(db: Database):
    # 1. Create token
    token = await db.create_society_token(
        society_code="TARUMT-AI",
        name="Artificial Intelligence Society",
        faculty="FOCS",
        is_private=False,
    )
    assert token.startswith("TC-")

    # 2. Retrieve token
    tok_data = await db.get_society_token(token)
    assert tok_data is not None
    assert tok_data["society_code"] == "TARUMT-AI"
    assert tok_data["status"] == "ACTIVE"
    assert tok_data["is_private"] == 0

    # 3. Redeem token
    success = await db.redeem_society_token(token, guild_id=12345, user_id=67890)
    assert success is True

    # 4. Cannot redeem twice
    success_again = await db.redeem_society_token(token, guild_id=12345, user_id=67890)
    assert success_again is False

    # 5. Revoke another token
    tok2 = await db.create_society_token("TARUMT-ROBO", "Robotics Club", "FOET")
    assert await db.revoke_society_token(tok2) is True
    tok2_data = await db.get_society_token(tok2)
    assert tok2_data["status"] == "REVOKED"


@pytest.mark.asyncio
async def test_applications_lifecycle(db: Database):
    app_id = await db.create_society_application(
        guild_id=987654321,
        guild_name="Cybersecurity Guild",
        applicant_id=11223344,
        applicant_name="Alice#1234",
        society_code="TARUMT-CYBER",
        name="Cybersecurity Club",
        faculty="FOCS",
        description="Official ethical hacking and cybersecurity club",
        is_private=False,
    )
    assert app_id > 0

    pending = await db.list_society_applications(status="PENDING")
    assert len(pending) == 1
    assert pending[0]["society_code"] == "TARUMT-CYBER"

    # Resolve application as APPROVED
    ok = await db.resolve_society_application(app_id, status="APPROVED", reviewer_id=999888)
    assert ok is True

    # Verify no longer pending
    pending_after = await db.list_society_applications(status="PENDING")
    assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_console_commands(db: Database, mock_bot, tmp_path: Path):
    settings = Settings(
        discord_token="fake_token",
        application_id=123456,
        database_path=tmp_path / "test.db",
        tarveri_bot_id=1542,
        enable_tarveri_integration=False,
        tarveri_db_path=None,
        shared_secret="secret",
        log_level="INFO",
        environment="test",
        enable_console=True,
    )

    console = ConsoleService(mock_bot, db, settings)

    # 1. Help command
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        await console.handle_command("help")
    assert "TARClub Console Commands" in captured.getvalue()

    # 2. Status command
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        await console.handle_command("status")
    assert "Bot User: TARClub#0001" in captured.getvalue()

    # 3. Token create and list command
    captured = io.StringIO()
    with patch("sys.stdout", captured):
        await console.handle_command("token create TARUMT-GD 'Game Dev Club' FOCS false")
    out = captured.getvalue()
    assert "Generated Registration Token: TC-" in out
    assert "TARUMT-GD" in out

    captured = io.StringIO()
    with patch("sys.stdout", captured):
        await console.handle_command("token list")
    assert "TARUMT-GD" in captured.getvalue()

    # 4. Clear command
    mock_stdout = MagicMock()
    mock_stdout.isatty.return_value = True
    with patch("sys.stdout", mock_stdout):
        await console.handle_command("clear")
        mock_stdout.write.assert_called_with("\033[2J\033[H")

    # 5. Stop command
    await console.handle_command("stop")
    assert mock_bot.close.called


def test_console_completer_tarclub():
    from tarclub.services.console_service import _console_completer

    patch_empty_buf = patch("readline.get_line_buffer", return_value="")
    patch_empty_idx = patch("readline.get_begidx", return_value=0)
    with patch_empty_buf, patch_empty_idx:
        # Empty line: matches top-level commands
        first = _console_completer("", 0)
        valid = (
            "help", "status", "guilds", "societies", "privacy", "token",
            "applications", "approve", "reject", "say", "eval", "clear",
            "stop", "exit", "quit"
        )
        assert first in valid

    patch_tok_buf = patch("readline.get_line_buffer", return_value="token ")
    patch_tok_idx = patch("readline.get_begidx", return_value=6)
    with patch_tok_buf, patch_tok_idx:
        # Subcommand token options
        assert _console_completer("", 0) == "create"
        assert _console_completer("", 1) == "list"
        assert _console_completer("", 2) == "revoke"

    patch_list_buf = patch("readline.get_line_buffer", return_value="token list ")
    patch_list_idx = patch("readline.get_begidx", return_value=11)
    with patch_list_buf, patch_list_idx:
        # Token list options
        assert _console_completer("", 0) == "active"
        assert _console_completer("", 1) == "used"
        assert _console_completer("", 2) == "revoked"

    patch_priv_buf = patch("readline.get_line_buffer", return_value="privacy 12345 ")
    patch_priv_idx = patch("readline.get_begidx", return_value=14)
    with patch_priv_buf, patch_priv_idx:
        # Privacy boolean options
        assert _console_completer("", 0) == "true"
        assert _console_completer("", 1) == "false"


def test_console_escable_help_tarclub(mock_bot, tmp_path: Path):
    settings = Settings(
        discord_token="fake_token",
        application_id=123456,
        database_path=tmp_path / "test.db",
        tarveri_bot_id=1542,
        enable_tarveri_integration=False,
        tarveri_db_path=None,
        shared_secret="secret",
        log_level="INFO",
        environment="test",
        enable_console=True,
    )
    console = ConsoleService(mock_bot, MagicMock(), settings)

    # In non-TTY fallback, outputs help text
    captured = io.StringIO()
    with patch("sys.stdout", captured), patch("sys.stdin.isatty", return_value=False):
        console._show_escable_help()
    assert "TARClub Console Commands" in captured.getvalue()
