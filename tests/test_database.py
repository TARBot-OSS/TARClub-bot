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
async def test_register_society_provisions_15_roles(test_db: Database):
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
    assert len(positions) == 15
    assert positions[0]["title"] == "Chairperson"
    assert positions[0]["hierarchy_order"] == 1
    assert positions[13]["title"] == "Internal Auditor"
    assert positions[13]["hierarchy_order"] == 14
    assert positions[14]["title"] == "General Member"
    assert positions[14]["department"] == "MEMBERSHIP"
    assert positions[14]["hierarchy_order"] == 15

    term = await test_db.get_active_term(society_id)
    assert term is not None
    assert term["term_name"] == "Term 2025/2026"
    assert term["is_active"] == 1


@pytest.mark.asyncio
async def test_society_privacy_and_public_directory(test_db: Database):
    # 1. Register Public Society
    pub_id = await test_db.register_society(
        guild_id=1001,
        society_code="TARUMT-PUB",
        name="Public Debate Society",
        faculty="FSSH",
        is_private=False,
    )
    assert pub_id > 0

    # 2. Register Private Society
    priv_id = await test_db.register_society(
        guild_id=1002,
        society_code="TARUMT-PRIV",
        name="Private Leadership Honor Society",
        faculty="ALL",
        is_private=True,
    )
    assert priv_id > 0

    # 3. Check public directory listing (should contain pub, but NOT priv)
    public_list = await test_db.list_public_societies()
    codes = [s["society_code"] for s in public_list]
    assert "TARUMT-PUB" in codes
    assert "TARUMT-PRIV" not in codes

    # 4. Toggle private society to public
    updated = await test_db.set_society_privacy(guild_id=1002, is_private=False)
    assert updated is True

    public_list_after = await test_db.list_public_societies()
    codes_after = [s["society_code"] for s in public_list_after]
    assert "TARUMT-PRIV" in codes_after
