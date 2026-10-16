"""
Unit tests for TARClub database schema and society registration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tarclub.database import Database


@pytest.fixture
async def test_db(tmp_path: Path):
    db_file = tmp_path / "test_tarclub.db"
    db = Database(db_file)
    await db.connect()
    await db.init_tables()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_register_society_provisions_14_roles(test_db: Database):
    guild_id = 999123456789
    society_id = await test_db.register_society(
        guild_id=guild_id,
        society_code="TARUMT-CS",
        name="Computer Science Society",
        faculty="FOCS",
        initial_term="2025/2026",
    )

    assert society_id is not None
    assert society_id > 0

    society = await test_db.get_society(guild_id)
    assert society is not None
    assert society["society_code"] == "TARUMT-CS"
    assert society["name"] == "Computer Science Society"
    assert society["faculty"] == "FOCS"
    assert society["primary_color"] == "#1E88E5"

    positions = await test_db.get_positions(society_id)
    assert len(positions) == 14
    assert positions[0]["title"] == "Chairperson"
    assert positions[0]["hierarchy_order"] == 1
    assert positions[13]["title"] == "Internal Auditor"
    assert positions[13]["hierarchy_order"] == 14

    term = await test_db.get_active_term(society_id)
    assert term is not None
    assert term["term_name"] == "Term 2025/2026"
    assert term["is_active"] == 1
