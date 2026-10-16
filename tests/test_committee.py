"""
Unit tests for TARClub committee rosters, RBAC bitmasks, theming, and annual handovers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tarclub.database import Database
from tarclub.services.rbac_service import SocietyPermission, has_permission


@pytest.fixture
async def db(tmp_path: Path):
    db_file = tmp_path / "test_tarclub_committee.db"
    database = Database(db_file)
    await database.connect()
    await database.init_tables()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_committee_assignment_and_rbac(db: Database):
    guild_id = 111222333444
    society_id = await db.register_society(
        guild_id=guild_id,
        society_code="TARUMT-IEESE",
        name="IEEE Student Branch",
        faculty="FOET",
        initial_term="2025/2026",
    )

    positions = await db.get_positions(society_id)
    chair_pos = next(p for p in positions if p["title"] == "Chairperson")
    term = await db.get_active_term(society_id)
    assert term is not None

    user_id = 555666777888

    # Assign user as Chairperson
    await db.assign_committee_member(
        term_id=term["id"],
        position_id=chair_pos["id"],
        user_id=user_id,
        student_id="24WMD99999",
        is_verified=True,
    )

    # Validate active roster reflects assignment
    roster = await db.get_active_roster(society_id)
    assigned = next(r for r in roster if r["title"] == "Chairperson")
    assert assigned["user_id"] == user_id
    assert assigned["student_id"] == "24WMD99999"
    assert assigned["is_verified_student"] == 1

    # Verify bitwise RBAC permissions for Chairperson
    user_perms = await db.get_user_permissions(society_id, user_id)
    assert has_permission(user_perms, SocietyPermission.MANAGE_SOCIETY) is True
    assert has_permission(user_perms, SocietyPermission.MANAGE_TERMS) is True
    assert has_permission(user_perms, SocietyPermission.ASSIGN_COMMITTEE) is True
    assert has_permission(user_perms, SocietyPermission.APPROVE_EXPENSES) is True

    # Test relieving committee member
    relieved = await db.relieve_committee_member(term["id"], chair_pos["id"], user_id)
    assert relieved is True
    perms_after = await db.get_user_permissions(society_id, user_id)
    assert perms_after == 0


@pytest.mark.asyncio
async def test_add_custom_position_and_update(db: Database):
    guild_id = 222333444555
    society_id = await db.register_society(
        guild_id=guild_id,
        society_code="TARUMT-GDSC",
        name="Google Developer Student Club",
        faculty="FOCS",
    )

    custom_id = await db.add_custom_position(
        society_id=society_id,
        title="AI Lead",
        department="DIRECTORSHIP",
        hierarchy_order=15,
        rbac_flags=SocietyPermission.DIRECTOR_CORE.value,
    )
    assert custom_id > 0

    positions = await db.get_positions(society_id)
    assert len(positions) == 16
    ai_pos = next(p for p in positions if p["title"] == "AI Lead")
    assert ai_pos["department"] == "DIRECTORSHIP"
    assert ai_pos["hierarchy_order"] == 15

    # Update position title
    await db.update_position(custom_id, new_title="Machine Learning Lead")
    updated_positions = await db.get_positions(society_id)
    ml_pos = next(p for p in updated_positions if p["id"] == custom_id)
    assert ml_pos["title"] == "Machine Learning Lead"


@pytest.mark.asyncio
async def test_normal_member_privilege_verified_vs_unverified(db: Database):
    guild_id = 998877665544
    society_id = await db.register_society(
        guild_id=guild_id,
        society_code="TARUMT-ROBOT",
        name="Robotics Society",
        faculty="FOET",
    )

    positions = await db.get_positions(society_id)
    member_pos = next(p for p in positions if p["title"] == "General Member")
    assert member_pos["hierarchy_order"] == 15
    assert member_pos["department"] == "MEMBERSHIP"

    term = await db.get_active_term(society_id)
    assert term is not None

    user_verified = 111122223333
    user_unverified = 444455556666

    # 1. Appoint verified student as General Member
    await db.assign_committee_member(
        term_id=term["id"],
        position_id=member_pos["id"],
        user_id=user_verified,
        student_id="25WMR01234",
        is_verified=True,
    )

    # 2. Appoint non-verified user as General Member
    await db.assign_committee_member(
        term_id=term["id"],
        position_id=member_pos["id"],
        user_id=user_unverified,
        is_verified=False,
    )

    # 3. Assert verified member permissions (full normal member bundle)
    perms_v = await db.get_user_permissions(society_id, user_verified)
    assert has_permission(perms_v, SocietyPermission.ACCESS_CLUB_CHAT) is True
    assert has_permission(perms_v, SocietyPermission.PARTICIPATE_EVENTS) is True
    assert has_permission(perms_v, SocietyPermission.VOTE_ELECTION) is True
    assert has_permission(perms_v, SocietyPermission.MANAGE_SOCIETY) is False
    assert has_permission(perms_v, SocietyPermission.APPROVE_EXPENSES) is False

    # 4. Assert non-verified member permissions (basic chat, NO voting or exclusive events)
    perms_uv = await db.get_user_permissions(society_id, user_unverified)
    assert has_permission(perms_uv, SocietyPermission.ACCESS_CLUB_CHAT) is True
    assert has_permission(perms_uv, SocietyPermission.PARTICIPATE_EVENTS) is False
    assert has_permission(perms_uv, SocietyPermission.VOTE_ELECTION) is False
    assert has_permission(perms_uv, SocietyPermission.MANAGE_SOCIETY) is False

    # 5. Verify active roster records
    roster = await db.get_active_roster(society_id)
    assigned_members = [r for r in roster if r["title"] == "General Member"]
    assert len(assigned_members) == 2

    m_v = next(m for m in assigned_members if m["user_id"] == user_verified)
    assert m_v["is_verified_student"] == 1
    assert m_v["student_id"] == "25WMR01234"

    m_uv = next(m for m in assigned_members if m["user_id"] == user_unverified)
    assert m_uv["is_verified_student"] == 0


@pytest.mark.asyncio
async def test_theme_customization(db: Database):
    guild_id = 333444555666
    society_id = await db.register_society(
        guild_id=guild_id,
        society_code="TARUMT-CULT",
        name="Cultural Society",
        faculty="ALL",
    )

    await db.update_theme(
        society_id=society_id,
        primary_color="#E53935",
        secondary_color="#00E676",
        emoji_badge="🎭",
        banner_url="https://example.com/banner.png",
        footer_text="Culture & Harmony",
    )

    society = await db.get_society(guild_id)
    assert society is not None
    assert society["primary_color"] == "#E53935"
    assert society["secondary_color"] == "#00E676"
    assert society["emoji_badge"] == "🎭"
    assert society["banner_url"] == "https://example.com/banner.png"


@pytest.mark.asyncio
async def test_annual_term_handover(db: Database):
    guild_id = 444555666777
    society_id = await db.register_society(
        guild_id=guild_id,
        society_code="TARUMT-BIZ",
        name="Business Society",
        faculty="FAFB",
        initial_term="2025/2026",
    )

    term1 = await db.get_active_term(society_id)
    assert term1 is not None
    assert term1["academic_year"] == "2025/2026"

    # Execute handover to 2026/2027
    new_term_id = await db.handover_term(
        society_id=society_id,
        current_term_id=term1["id"],
        next_academic_year="2026/2027",
        handover_notes="AGM 2026 election completed cleanly.",
    )
    assert new_term_id > term1["id"]

    # Verify active term switched
    term2 = await db.get_active_term(society_id)
    assert term2 is not None
    assert term2["academic_year"] == "2026/2027"
    assert term2["term_name"] == "Term 2026/2027"
    assert term2["is_active"] == 1

    # Check term history
    history = await db.get_term_history(society_id)
    assert len(history) == 2
    assert history[0]["academic_year"] == "2026/2027"
    assert history[0]["is_active"] == 1
    assert history[1]["academic_year"] == "2025/2026"
    assert history[1]["is_active"] == 0
    assert history[1]["handover_notes"] == "AGM 2026 election completed cleanly."
