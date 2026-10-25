"""
Cross-Bot Integration Service bridging TARClub and TARVeri.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import sqlite3
import time
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any

from tarbot_console.contracts import TARVERI_SCHEMA_VERSION, TARVERI_STUDENT_ROLE_NAMES

if TYPE_CHECKING:
    from pathlib import Path

    import discord

logger = logging.getLogger("tarclub.services.integration")

# Columns read from TARVeri's verifications table. Each must be in tarbot_console.contracts.
TARVERI_READ_COLUMNS = (
    "discord_user_id",
    "faculty_code",
    "campus_code",
    "level_code",
    "programme_code",
    "is_alumni",
    "card_expiry_date",
    "verified_at",
)

# Schema versions already warned about, so a mismatch is logged once per process.
_warned_schema_versions: set[int] = set()


class IntegrationService:
    """Orchestrates cross-bot presence discovery, token verification, and student gating."""

    @staticmethod
    def is_tarveri_present(guild: discord.Guild, tarveri_id: int) -> bool:
        """Check if TARVeri bot is present in the Discord server."""
        return guild.get_member(tarveri_id) is not None

    @staticmethod
    def is_bridge_paired(integration: dict[str, Any] | None) -> bool:
        """A society is paired only once a TARVeri pairing token has been redeemed."""
        return bool(integration and integration.get("shared_secret_hash"))

    @staticmethod
    def validate_pairing_token(token: str, guild_id: int, secret: str) -> tuple[bool, str]:
        """Validate a TARVeri-issued society pairing token for a Discord guild.

        Format: TARV-<society_guild_id>-<timestamp>-<sig>, where sig is the first 32 hex chars of
        HMAC-SHA256(secret, "<society_guild_id>:<timestamp>"), as in
        tarveri.config.sign_society_pairing.
        """
        if not secret:
            return False, "TARCLUB_SHARED_SECRET is not configured; cannot verify pairing tokens."
        parts = token.strip().split("-")
        if len(parts) != 4 or parts[0] != "TARV" or not (parts[1].isdigit() and parts[2].isdigit()):
            return False, "Invalid token structure (expected TARV-<guild>-<ts>-<signature>)."
        _, token_guild_str, timestamp_str, sig = parts
        if int(token_guild_str) != guild_id:
            return (
                False,
                f"Token was issued for guild {token_guild_str}, not this server ({guild_id}).",
            )
        expected = hmac.new(
            secret.encode("utf-8"), f"{token_guild_str}:{timestamp_str}".encode(), hashlib.sha256
        ).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return False, "Invalid cryptographic token signature."
        if abs(int(time.time()) - int(timestamp_str)) > 86400:
            return False, "Token has expired (tokens are valid for 24 hours)."
        return True, "Token validated successfully."

    @staticmethod
    def query_co_located_tarveri_students(
        tarveri_db_path: Path | None, discord_ids: Collection[int]
    ) -> dict[int, dict[str, Any]]:
        """Batch-read co-located TARVeri rows by Discord ID. Blocking: use asyncio.to_thread."""
        if not discord_ids or tarveri_db_path is None:
            return {}
        if not tarveri_db_path.exists() or not tarveri_db_path.is_file():
            return {}
        ids = list(discord_ids)
        placeholders = ",".join("?" * len(ids))
        try:
            uri = f"file:{tarveri_db_path.resolve()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version != TARVERI_SCHEMA_VERSION and version not in _warned_schema_versions:
                    _warned_schema_versions.add(version)
                    logger.warning(
                        "TARVeri database schema version is %s; TARClub expects %s. "
                        "Check the verifications columns.",
                        version,
                        TARVERI_SCHEMA_VERSION,
                    )
                select_columns = ", ".join(TARVERI_READ_COLUMNS)
                rows = conn.execute(
                    f"SELECT {select_columns} FROM verifications "
                    f"WHERE discord_user_id IN ({placeholders});",
                    ids,
                ).fetchall()
                return {row["discord_user_id"]: dict(row) for row in rows}
        except sqlite3.Error as e:
            logger.warning("Direct TARVeri SQLite query failed (schema drift or locked DB?): %s", e)
            return {}

    @staticmethod
    def verify_member_student_status(
        member: discord.Member,
        tarveri_rows: Mapping[int, dict[str, Any]] | None = None,
    ) -> tuple[bool, str | None]:
        """Verify whether a Discord member is a TARVeri-verified student.

        Checks member roles first, then the pre-fetched co-located TARVeri rows keyed by Discord ID.
        Returns: (is_verified, faculty_or_role_name)
        """
        for role in member.roles:
            role_name_clean = role.name.lower().strip().removeprefix("tarumt ")
            if role_name_clean in TARVERI_STUDENT_ROLE_NAMES:
                return True, role.name

        db_res = tarveri_rows.get(member.id) if tarveri_rows else None
        if db_res:
            fac = db_res.get("faculty_code") or "TAR UMT Student"
            return True, f"DB Verified ({fac})"

        return False, None
