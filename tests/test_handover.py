import sqlite3
from pathlib import Path

import pytest

from tarclub.database import Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_tarclub_handover.db")
    await database.connect()
    await database.init_tables()
    yield database
    await database.close()


async def _society_with_term(db: Database, guild_id: int) -> tuple[int, dict]:
    society_id = await db.register_society(
        guild_id=guild_id,
        society_code="TARUMT-HO",
        name="Handover Society",
        faculty="FAFB",
        initial_term="2025/2026",
    )
    term = await db.get_active_term(society_id)
    assert term is not None
    return society_id, term


@pytest.mark.asyncio
async def test_proposer_cannot_confirm_own_handover(db: Database):
    society_id, term = await _society_with_term(db, 900000000001)
    await db.propose_handover(society_id, term["id"], "2026/2027", None, proposed_by=1)

    assert await db.confirm_handover(society_id, confirmed_by=1) is None

    assert (await db.get_active_term(society_id))["academic_year"] == "2025/2026"
    assert await db.get_pending_handover(society_id) is not None


@pytest.mark.asyncio
async def test_second_member_confirms_and_handover_executes_once(db: Database):
    society_id, term = await _society_with_term(db, 900000000002)
    await db.propose_handover(society_id, term["id"], "2026/2027", "AGM resolved", proposed_by=1)

    new_term_id = await db.confirm_handover(society_id, confirmed_by=2)
    assert new_term_id is not None

    active = await db.get_active_term(society_id)
    assert active["academic_year"] == "2026/2027"
    assert await db.get_pending_handover(society_id) is None
    history = await db.get_term_history(society_id)
    assert history[1]["handover_notes"] == "AGM resolved"

    assert await db.confirm_handover(society_id, confirmed_by=3) is None
    assert (await db.get_active_term(society_id))["id"] == new_term_id


@pytest.mark.asyncio
async def test_only_one_pending_handover_per_society(db: Database):
    society_id, term = await _society_with_term(db, 900000000003)
    await db.propose_handover(society_id, term["id"], "2026/2027", None, proposed_by=1)

    with pytest.raises(sqlite3.IntegrityError):
        await db.propose_handover(society_id, term["id"], "2027/2028", None, proposed_by=2)


@pytest.mark.asyncio
async def test_cancel_clears_pending_and_allows_new_proposal(db: Database):
    society_id, term = await _society_with_term(db, 900000000004)
    await db.propose_handover(society_id, term["id"], "2026/2027", None, proposed_by=1)

    assert await db.cancel_handover(society_id, cancelled_by=1) is True
    assert await db.get_pending_handover(society_id) is None
    assert await db.cancel_handover(society_id, cancelled_by=1) is False

    await db.propose_handover(society_id, term["id"], "2026/2027", None, proposed_by=2)
    assert await db.get_pending_handover(society_id) is not None
