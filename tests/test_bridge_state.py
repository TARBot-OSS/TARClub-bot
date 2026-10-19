from pathlib import Path

import pytest

from tarclub.database import Database
from tarclub.services.integration_service import IntegrationService


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_bridge_state.db")
    await database.connect()
    await database.init_tables()
    yield database
    await database.close()


async def test_toggle_enforce_does_not_mark_bridge_paired(db: Database):
    await db.set_enforce_student(900001, enabled=True)

    integration = await db.get_integration(900001)
    assert integration["enforce_student_verification"] == 1
    assert integration["is_linked"] == 0
    assert IntegrationService.is_bridge_paired(integration) is False


async def test_token_redemption_marks_bridge_paired(db: Database):
    await db.set_integration(
        guild_id=900002, is_linked=True, enforce_student=True, shared_secret_hash="abc123"
    )

    assert IntegrationService.is_bridge_paired(await db.get_integration(900002)) is True


async def test_toggle_after_pairing_keeps_pairing(db: Database):
    await db.set_integration(
        guild_id=900003, is_linked=True, enforce_student=True, shared_secret_hash="abc123"
    )
    await db.set_enforce_student(900003, enabled=False)

    integration = await db.get_integration(900003)
    assert integration["enforce_student_verification"] == 0
    assert integration["shared_secret_hash"] == "abc123"
    assert IntegrationService.is_bridge_paired(integration) is True


def test_no_integration_row_is_not_paired():
    assert IntegrationService.is_bridge_paired(None) is False
