"""TARClub's reads from TARVeri must match tarbot_console.contracts.

TARVeri's tests check that its side of the same contract holds.
"""

import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from tarbot_console.contracts import TARVERI_SCHEMA_VERSION, TARVERI_VERIFICATION_COLUMNS

from tarclub.services import integration_service
from tarclub.services.integration_service import TARVERI_READ_COLUMNS, IntegrationService


def _tarveri_db(path: Path, user_version: int) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE verifications (
                discord_user_id INTEGER PRIMARY KEY,
                student_id_hash TEXT,
                faculty_code TEXT,
                campus_code TEXT,
                level_code TEXT,
                programme_code TEXT,
                is_alumni INTEGER DEFAULT 0,
                card_expiry_date TEXT,
                verified_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO verifications (discord_user_id, faculty_code, verified_at) "
            "VALUES (42, 'FOCS', '2026-01-01')"
        )
        conn.execute(f"PRAGMA user_version = {user_version}")
    return path


def test_reads_only_columns_in_the_contract():
    assert set(TARVERI_READ_COLUMNS) <= TARVERI_VERIFICATION_COLUMNS


def test_query_reads_a_database_at_the_contract_version(tmp_path):
    db = _tarveri_db(tmp_path / "tarveri.db", TARVERI_SCHEMA_VERSION)
    rows = IntegrationService.query_co_located_tarveri_students(db, [42])
    assert rows[42]["faculty_code"] == "FOCS"


def test_schema_version_mismatch_warns_once_and_still_reads(tmp_path, caplog):
    integration_service._warned_schema_versions.clear()
    db = _tarveri_db(tmp_path / "newer.db", TARVERI_SCHEMA_VERSION + 1)

    with caplog.at_level(logging.WARNING, logger="tarclub.services.integration"):
        first = IntegrationService.query_co_located_tarveri_students(db, [42])
        second = IntegrationService.query_co_located_tarveri_students(db, [42])

    assert first[42]["faculty_code"] == "FOCS"
    assert second[42]["faculty_code"] == "FOCS"
    warnings = [r for r in caplog.records if "schema version" in r.getMessage()]
    assert len(warnings) == 1


def test_matching_schema_version_is_silent(tmp_path, caplog):
    integration_service._warned_schema_versions.clear()
    db = _tarveri_db(tmp_path / "same.db", TARVERI_SCHEMA_VERSION)

    with caplog.at_level(logging.WARNING, logger="tarclub.services.integration"):
        IntegrationService.query_co_located_tarveri_students(db, [42])

    assert not [r for r in caplog.records if "schema version" in r.getMessage()]


def test_degree_level_role_marks_a_verified_student():
    role = MagicMock()
    role.name = "Degree"
    member = MagicMock()
    member.id = 7
    member.roles = [role]

    assert IntegrationService.verify_member_student_status(member) == (True, "Degree")
