"""
Cross-Bot Integration Service bridging TARClub and TARVeri.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import sqlite3
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    import discord

logger = logging.getLogger("tarclub.services.integration")

# Exact role names TARVeri assigns to verified students (matched case-insensitively,
# with an optional "TARUMT " prefix). Guest and organizational roles never match.
TARVERI_STUDENT_ROLE_NAMES = {
    "verified",
    "fafb",
    "cpus",
    "focs",
    "fcci",
    "foas",
    "fobe",
    "fssh",
    "foet",
    "bachelor's degree",
    "diploma",
    "foundation",
    "postgraduate",
}


class IntegrationService:
    """Orchestrates cross-bot presence discovery, token verification, and student gating."""

    @staticmethod
    def is_tarveri_present(guild: discord.Guild, tarveri_id: int) -> bool:
        """Check if TARVeri bot is present in the Discord server."""
        return guild.get_member(tarveri_id) is not None

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
    def query_co_located_tarveri_student(
        tarveri_db_path: Path, discord_id: int
    ) -> dict[str, Any] | None:
        """Query TARVeri SQLite database in read-only mode if co-located on the same filesystem."""
        if not tarveri_db_path.exists() or not tarveri_db_path.is_file():
            return None
        try:
            uri = f"file:{tarveri_db_path.resolve()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT discord_user_id, faculty_code, campus_code, level_code,
                           programme_code, is_alumni, card_expiry_date, verified_at
                    FROM verifications
                    WHERE discord_user_id = ?;
                    """,
                    (discord_id,),
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
        except sqlite3.Error as e:
            logger.warning("Direct TARVeri SQLite query failed (schema drift or locked DB?): %s", e)
            return None

    @staticmethod
    def verify_member_student_status(
        member: discord.Member,
        tarveri_db_path: Path | None = None,
    ) -> tuple[bool, str | None]:
        """Verify whether a Discord member has been verified as a student by TARVeri.

        1. Checks member roles against TARVeri faculty and student role indicators.
        2. If not found and tarveri_db_path is configured and readable, queries co-located DB.
        Returns: (is_verified, faculty_or_role_name)
        """
        for role in member.roles:
            role_name_clean = role.name.lower().strip().removeprefix("tarumt ")
            if role_name_clean in TARVERI_STUDENT_ROLE_NAMES:
                return True, role.name

        if tarveri_db_path:
            db_res = IntegrationService.query_co_located_tarveri_student(tarveri_db_path, member.id)
            if db_res:
                fac = db_res.get("faculty_code") or "TAR UMT Student"
                return True, f"DB Verified ({fac})"

        return False, None
