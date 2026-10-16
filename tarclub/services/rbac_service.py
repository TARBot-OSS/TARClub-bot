"""
Bitwise Role-Based Access Control (RBAC) service for TARClub.
"""

from __future__ import annotations

import enum


class SocietyPermission(enum.IntFlag):
    """Granular bitwise permission flags for society committee roles."""

    NONE = 0
    MANAGE_SOCIETY = 1 << 0  # 0x0001: Edit society profile, theme & settings
    MANAGE_TERMS = 1 << 1  # 0x0002: Create terms, initiate handovers
    MANAGE_POSITIONS = 1 << 2  # 0x0004: Add, edit, reorder or delete positions
    ASSIGN_COMMITTEE = 1 << 3  # 0x0008: Assign or dismiss committee members
    POST_ANNOUNCEMENTS = 1 << 4  # 0x0010: Publish verified club-wide broadcasts
    CREATE_EVENTS = 1 << 5  # 0x0020: Schedule and manage club calendar events
    MANAGE_BUDGET = 1 << 6  # 0x0040: Propose or modify budget line items
    APPROVE_EXPENSES = 1 << 7  # 0x0080: Authorize reimbursements & financial sign-off
    AUDIT_LOGS = 1 << 8  # 0x0100: View full governance & financial audit trails
    MANAGE_ASSETS = 1 << 9  # 0x0200: Manage club equipment and technical resources
    EXPORT_DATA = 1 << 10  # 0x0400: Export member rosters & term reports (CSV/PDF)
    BYPASS_STUDENT_GATE = 1 << 11  # 0x0800: Override student verification requirement
    ACCESS_CLUB_CHAT = 1 << 12  # 0x1000: Standard access to club discussion & activities
    PARTICIPATE_EVENTS = 1 << 13  # 0x2000: Register & participate in member-exclusive events
    VOTE_ELECTION = 1 << 14  # 0x4000: AGM & General election voting rights (verified only)

    # Pre-defined permission bundles
    ALL = (1 << 15) - 1  # 0x7FFF: Full administrative & member privileges
    EXECUTIVE_CORE = (
        MANAGE_SOCIETY
        | MANAGE_TERMS
        | ASSIGN_COMMITTEE
        | POST_ANNOUNCEMENTS
        | CREATE_EVENTS
        | AUDIT_LOGS
        | ACCESS_CLUB_CHAT
        | PARTICIPATE_EVENTS
        | VOTE_ELECTION
    )
    SECRETARIAT_CORE = (
        POST_ANNOUNCEMENTS
        | CREATE_EVENTS
        | AUDIT_LOGS
        | EXPORT_DATA
        | ACCESS_CLUB_CHAT
        | PARTICIPATE_EVENTS
        | VOTE_ELECTION
    )
    FINANCE_CORE = (
        MANAGE_BUDGET
        | APPROVE_EXPENSES
        | AUDIT_LOGS
        | EXPORT_DATA
        | ACCESS_CLUB_CHAT
        | PARTICIPATE_EVENTS
        | VOTE_ELECTION
    )
    DIRECTOR_CORE = (
        CREATE_EVENTS | POST_ANNOUNCEMENTS | ACCESS_CLUB_CHAT | PARTICIPATE_EVENTS | VOTE_ELECTION
    )
    # Normal member permission bundles:
    MEMBER_CORE = ACCESS_CLUB_CHAT | PARTICIPATE_EVENTS | VOTE_ELECTION  # Verified student
    NON_VERIFIED_MEMBER = ACCESS_CLUB_CHAT  # Non-verified / Guest member (no voting/events)


def has_permission(user_mask: int, required_permission: SocietyPermission) -> bool:
    """Check if bitmask contains the required permission."""
    return bool(user_mask & required_permission.value)


def aggregate_permissions(position_masks: list[int]) -> int:
    """Combine permissions across multiple held positions."""
    total = 0
    for mask in position_masks:
        total |= mask
    return total
