"""
Cross-Bot Integration Service bridging TARClub and TARVeri.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    """Orchestrates cross-bot presence discovery and student verification checks."""

    @staticmethod
    def is_tarveri_present(guild: discord.Guild, tarveri_id: int) -> bool:
        """Check if TARVeri bot is present in the Discord server."""
        return guild.get_member(tarveri_id) is not None

    @staticmethod
    def verify_member_student_status(member: discord.Member) -> tuple[bool, str | None]:
        """Verify whether a Discord member has been verified as a student by TARVeri.

        Checks member roles against TARVeri faculty and student role indicators.
        Returns: (is_verified, faculty_or_role_name)
        """
        for role in member.roles:
            role_name_clean = role.name.lower().strip()
            if any(keyword in role_name_clean for keyword in TARVERI_STUDENT_ROLE_KEYWORDS):
                # Ensure it's not a guest role
                if "guest" not in role_name_clean:
                    return True, role.name

        return False, None
