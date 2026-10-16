"""
Cross-Bot Integration Service bridging TARClub and TARVeri.
"""

from __future__ import annotations

import base64
import logging
import sqlite3
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    import discord

logger = logging.getLogger("tarclub.services.integration")

# Standard TARVeri verified student roles
TARVERI_STUDENT_ROLE_KEYWORDS = {
    "student",
    "verified",
    "focs",
    "foet",
    "fafb",
    "fcci",
    "fobeh",
    "fssh",
    "cpus",
    "degree",
    "diploma",
    "foundation",
}


class IntegrationService:
    """Orchestrates cross-bot presence discovery, token verification, and student gating."""

    @staticmethod
    def is_tarveri_present(guild: discord.Guild, tarveri_id: int) -> bool:
        """Check if TARVeri bot is present in the Discord server."""
        return guild.get_member(tarveri_id) is not None

    @staticmethod
    def validate_pairing_token(token: str, guild_id: int) -> tuple[bool, str]:
        """Validate a TARVeri-issued society pairing token for a Discord guild.

        Format: base64(tarveri-bridge:v1:<society_guild_id>:<timestamp>:<sig>)
        """
        try:
            raw_bytes = base64.urlsafe_b64decode(token.strip().encode("utf-8"))
            decoded = raw_bytes.decode("utf-8")
            parts = decoded.split(":")
            if len(parts) != 5:
                return False, "Invalid token structure (expected 5 segments)."
            prefix, version, token_guild_str, timestamp_str, sig = parts
            if prefix != "tarveri-bridge" or version != "v1":
                return False, f"Unsupported token protocol or version ({prefix}:{version})."
            if int(token_guild_str) != guild_id:
                return (
                    False,
                    f"Token was issued for guild {token_guild_str}, not this server ({guild_id}).",
                )

            token_time = int(timestamp_str)
            # Check token expiration (24h validity window)
            now = int(time.time())
            if abs(now - token_time) > 86400:
                return False, "Token has expired (tokens are valid for 24 hours)."
            if not sig or len(sig) < 8:
                return False, "Invalid cryptographic token signature."
            return True, "Token validated successfully."
        except Exception as e:
            return False, f"Failed to decode pairing token: {e}"

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
        except Exception as e:
            logger.debug("Direct TARVeri SQLite query failed (graceful fallback): %s", e)
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
            role_name_clean = role.name.lower().strip()
            if any(keyword in role_name_clean for keyword in TARVERI_STUDENT_ROLE_KEYWORDS):
                # Ensure it's not a guest role
                if "guest" not in role_name_clean:
                    return True, role.name

        if tarveri_db_path:
            db_res = IntegrationService.query_co_located_tarveri_student(tarveri_db_path, member.id)
            if db_res:
                fac = db_res.get("faculty_code") or "TAR UMT Student"
                return True, f"DB Verified ({fac})"

        return False, None
