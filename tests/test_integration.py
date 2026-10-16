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
    role_focs.name = "FOCS Student"
    member.roles = [role_focs]

    is_verified, role_name = IntegrationService.verify_member_student_status(member)
    assert is_verified is True
    assert role_name == "FOCS Student"

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
