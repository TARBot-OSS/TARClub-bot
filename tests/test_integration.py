"""
Unit tests for TARVeri cross-bot discovery, student verification checks, and bridge settings.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tarclub.database import Database
from tarclub.services.integration_service import IntegrationService


@pytest.fixture
async def db(tmp_path: Path):
    db_file = tmp_path / "test_tarclub_integration.db"
    database = Database(db_file)
    await database.connect()
    await database.init_tables()
    yield database
    await database.close()


def test_is_tarveri_present_detection():
    tarveri_bot_id = 1542166264561016963

    guild_mock = MagicMock()
    guild_mock.get_member.return_value = MagicMock(id=tarveri_bot_id)

    assert IntegrationService.is_tarveri_present(guild_mock, tarveri_bot_id) is True

    guild_empty = MagicMock()
    guild_empty.get_member.return_value = None
    assert IntegrationService.is_tarveri_present(guild_empty, tarveri_bot_id) is False


def test_verify_member_student_status_faculty():
    member = MagicMock()

    # Case 1: Member with FOCS faculty role
    role_focs = MagicMock()
    role_focs.name = "FOCS"
    member.roles = [role_focs]

    is_verified, role_name = IntegrationService.verify_member_student_status(member)
    assert is_verified is True
    assert role_name == "FOCS"

    # Case 1b: Organizational look-alike roles must not count as verified
    role_council = MagicMock()
    role_council.name = "Student Council"
    member.roles = [role_council]
    assert IntegrationService.verify_member_student_status(member)[0] is False
    member.roles = [role_focs]

    # Case 2: Member with Guest role should NOT be verified as student
    role_guest = MagicMock()
    role_guest.name = "Guest(Approved)"
    member.roles = [role_guest]

    is_verified_guest, _ = IntegrationService.verify_member_student_status(member)
    assert is_verified_guest is False


@pytest.mark.asyncio
async def test_integration_db_persistence(db: Database):
    guild_id = 987654321012

    # Initial state should be None
    state = await db.get_integration(guild_id)
    assert state is None

    # Link guild
    await db.set_integration(guild_id=guild_id, is_linked=True, enforce_student=True)
    linked_state = await db.get_integration(guild_id)
    assert linked_state is not None
    assert linked_state["is_linked"] == 1
    assert linked_state["enforce_student_verification"] == 1

    # Toggle enforcement off
    await db.set_integration(guild_id=guild_id, is_linked=True, enforce_student=False)
    unlocked_state = await db.get_integration(guild_id)
    assert unlocked_state is not None
    assert unlocked_state["enforce_student_verification"] == 0


def _signed_token(guild_id: int, ts: int, secret: str) -> str:
    import hashlib
    import hmac

    sig = hmac.new(secret.encode(), f"{guild_id}:{ts}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"TARV-{guild_id}-{ts}-{sig}"


def test_pairing_token_validation():
    import time

    guild_id = 1234567890
    secret = "s3cret"
    now = int(time.time())
    valid_token = _signed_token(guild_id, now, secret)

    ok, msg = IntegrationService.validate_pairing_token(valid_token, guild_id, secret)
    assert ok is True
    assert "successfully" in msg

    # Mismatched guild
    ok, err = IntegrationService.validate_pairing_token(valid_token, 9999999999, secret)
    assert ok is False
    assert "not this server" in err

    # Forged signature (wrong secret) and unconfigured secret
    ok, err = IntegrationService.validate_pairing_token(valid_token, guild_id, "other")
    assert ok is False
    assert "signature" in err
    ok, err = IntegrationService.validate_pairing_token(valid_token, guild_id, "")
    assert ok is False
    assert "not configured" in err

    # Expired token
    old_token = _signed_token(guild_id, now - 100000, secret)
    ok, err = IntegrationService.validate_pairing_token(old_token, guild_id, secret)
    assert ok is False
    assert "expired" in err

    # Invalid token format
    ok, err = IntegrationService.validate_pairing_token("not-a-valid-token", guild_id, secret)
    assert ok is False


def test_co_located_tarveri_student_query(tmp_path: Path):
    import sqlite3

    tarveri_db = tmp_path / "tarveri_mock.db"
    with sqlite3.connect(tarveri_db) as conn:
        conn.execute(
            """
            CREATE TABLE verifications (
                discord_user_id INTEGER PRIMARY KEY,
                student_id_hash TEXT UNIQUE NOT NULL,
                faculty_code TEXT NOT NULL,
                campus_code TEXT,
                level_code TEXT,
                programme_code TEXT,
                is_alumni INTEGER DEFAULT 0,
                card_expiry_date TEXT,
                verified_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO verifications (
                discord_user_id, student_id_hash, faculty_code, campus_code, verified_at
            ) VALUES (11223344, 'hash123', 'FOCS', 'W', '2026-10-16 20:00:00');
            """
        )
        conn.commit()

    # Batch query returns only existing users, keyed by Discord ID
    rows = IntegrationService.query_co_located_tarveri_students(tarveri_db, [11223344, 99999999])
    assert set(rows) == {11223344}
    assert rows[11223344]["faculty_code"] == "FOCS"
    assert rows[11223344]["campus_code"] == "W"

    assert IntegrationService.query_co_located_tarveri_students(tarveri_db, []) == {}

    # Missing db file yields no rows instead of raising
    missing = tmp_path / "missing.db"
    assert IntegrationService.query_co_located_tarveri_students(missing, [11223344]) == {}

    # Test member status via pre-fetched co-located rows
    member_mock = MagicMock()
    member_mock.id = 11223344
    member_mock.roles = []  # No discord roles
    is_v, label = IntegrationService.verify_member_student_status(member_mock, rows)
    assert is_v is True
    assert "FOCS" in str(label)
