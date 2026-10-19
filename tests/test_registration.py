from pathlib import Path

import pytest

from tarclub.database import Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_tarclub_registration.db")
    await database.connect()
    await database.init_tables()
    yield database
    await database.close()


async def test_registration_token_is_single_use(db: Database):
    token = await db.create_society_token("TARUMT-ROB", "Robotics Club", faculty="FOCS")

    tok = await db.get_society_token(token)
    assert tok is not None
    assert tok["status"] == "ACTIVE"

    assert await db.redeem_society_token(token, guild_id=1001, user_id=5001) is True
    assert await db.redeem_society_token(token, guild_id=1002, user_id=5002) is False

    used = await db.get_society_token(token)
    assert used["status"] == "USED"
    assert used["used_by_guild_id"] == 1001


async def test_revoked_token_cannot_be_redeemed(db: Database):
    token = await db.create_society_token("TARUMT-DBG", "Debate Club")

    assert await db.revoke_society_token(token) is True
    assert await db.redeem_society_token(token, guild_id=1003, user_id=5003) is False
    assert await db.revoke_society_token(token) is False
    assert (await db.get_society_token(token))["status"] == "REVOKED"


async def test_application_approval_resolves_once(db: Database):
    app_id = await db.create_society_application(
        guild_id=2001,
        guild_name="New Hub",
        applicant_id=6001,
        applicant_name="applicant",
        society_code="TARUMT-HUB",
        name="Innovation Hub",
        faculty="FCI",
    )

    pending = await db.list_society_applications("PENDING")
    assert [a["id"] for a in pending] == [app_id]

    assert await db.resolve_society_application(app_id, "approved", reviewer_id=7001) is True
    assert await db.resolve_society_application(app_id, "REJECTED", reviewer_id=7002) is False

    resolved = await db.get_society_application(app_id)
    assert resolved["status"] == "APPROVED"
    assert resolved["reviewed_by"] == 7001
    assert await db.list_society_applications("PENDING") == []


async def test_application_rejection_keeps_reason(db: Database):
    app_id = await db.create_society_application(
        guild_id=2002,
        guild_name="Duplicate Club",
        applicant_id=6002,
        applicant_name="applicant",
        society_code="TARUMT-DUP",
        name="Duplicate Society",
    )

    assert await db.resolve_society_application(
        app_id, "rejected", reviewer_id=7003, reason="Overlaps an existing society"
    )

    rejected = await db.get_society_application(app_id)
    assert rejected["status"] == "REJECTED"
    assert rejected["review_reason"] == "Overlaps an existing society"
