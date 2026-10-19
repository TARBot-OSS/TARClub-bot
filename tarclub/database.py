"""
TARClub Database façade coordinating domain schemas and repository mixins.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from tarclub.db.base import DatabaseBase
from tarclub.services.rbac_service import SocietyPermission

logger = logging.getLogger("tarclub.database")

# Standard 15-Role Starter Template including leadership and general membership
DEFAULT_TEMPLATE_POSITIONS = [
    {"title": "Chairperson", "department": "PRESIDIUM", "hierarchy": 1, "rbac": 0x7FFF},
    {"title": "Vice Chairperson", "department": "PRESIDIUM", "hierarchy": 2, "rbac": 0x77FF},
    {"title": "Deputy Chairperson", "department": "PRESIDIUM", "hierarchy": 3, "rbac": 0x77FF},
    {"title": "Deputy Vice Chairperson", "department": "PRESIDIUM", "hierarchy": 4, "rbac": 0x77FF},
    {"title": "Secretary", "department": "SECRETARIAT", "hierarchy": 5, "rbac": 0x751F},
    {"title": "Deputy Secretary", "department": "SECRETARIAT", "hierarchy": 6, "rbac": 0x7513},
    {"title": "Treasurer", "department": "FINANCE", "hierarchy": 7, "rbac": 0x75D0},
    {"title": "Deputy Treasurer", "department": "FINANCE", "hierarchy": 8, "rbac": 0x7550},
    {
        "title": "Programme & Activities Director",
        "department": "DIRECTORSHIP",
        "hierarchy": 9,
        "rbac": 0x7030,
    },
    {
        "title": "Technical Resource Director",
        "department": "DIRECTORSHIP",
        "hierarchy": 10,
        "rbac": 0x7210,
    },
    {"title": "Publicity Director", "department": "DIRECTORSHIP", "hierarchy": 11, "rbac": 0x7010},
    {"title": "Publicity Member", "department": "DIRECTORSHIP", "hierarchy": 12, "rbac": 0x7000},
    {
        "title": "Art and Promotion Director",
        "department": "DIRECTORSHIP",
        "hierarchy": 13,
        "rbac": 0x7010,
    },
    {"title": "Internal Auditor", "department": "DIRECTORSHIP", "hierarchy": 14, "rbac": 0x7100},
    {"title": "General Member", "department": "MEMBERSHIP", "hierarchy": 15, "rbac": 0x7000},
]


class Database(DatabaseBase):
    """Primary Database instance for TARClub."""

    async def init_tables(self) -> None:
        """Create tables if not exists and apply schema migrations."""
        await self.conn.executescript(
            """
            -- 1. Societies registered per guild
            CREATE TABLE IF NOT EXISTS societies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER UNIQUE NOT NULL,
                society_code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                faculty TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'ACADEMIC',
                is_private BOOLEAN DEFAULT 0,
                description TEXT,
                founded_year INTEGER,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- 2. Society branding & dynamic themes
            CREATE TABLE IF NOT EXISTS society_themes (
                society_id INTEGER PRIMARY KEY,
                primary_color TEXT DEFAULT '#1E88E5',
                secondary_color TEXT DEFAULT '#FFC107',
                banner_url TEXT,
                logo_url TEXT,
                emoji_badge TEXT DEFAULT '🏛️',
                footer_text TEXT DEFAULT 'TAR UMT Society Management System',
                FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE CASCADE
            );

            -- 3. Annual terms (e.g. 2025/2026)
            CREATE TABLE IF NOT EXISTS terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                society_id INTEGER NOT NULL,
                term_name TEXT NOT NULL,
                academic_year TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_date TIMESTAMP,
                handover_at TIMESTAMP,
                handover_notes TEXT,
                FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE CASCADE,
                UNIQUE(society_id, term_name)
            );

            -- 4. Dynamic committee positions & templates
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                society_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                department TEXT NOT NULL,
                hierarchy_order INTEGER NOT NULL,
                discord_role_id INTEGER,
                rbac_flags INTEGER NOT NULL DEFAULT 0,
                is_template BOOLEAN DEFAULT 0,
                FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE CASCADE,
                UNIQUE(society_id, title)
            );

            -- 5. Committee roster per term
            CREATE TABLE IF NOT EXISTS committee_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term_id INTEGER NOT NULL,
                position_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                student_id TEXT,
                is_verified_student BOOLEAN DEFAULT 0,
                appointed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                relieved_at TIMESTAMP,
                FOREIGN KEY (term_id) REFERENCES terms(id) ON DELETE CASCADE,
                FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE,
                UNIQUE(term_id, position_id, user_id)
            );

            -- 6. Cross-Bot integration bridge
            CREATE TABLE IF NOT EXISTS tarveri_integrations (
                guild_id INTEGER PRIMARY KEY,
                is_linked BOOLEAN DEFAULT 0,
                paired_at TIMESTAMP,
                shared_secret_hash TEXT,
                enforce_student_verification BOOLEAN DEFAULT 1
            );

            -- 7. One-Time Society Registration Tokens (Console generated)
            CREATE TABLE IF NOT EXISTS society_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                society_code TEXT NOT NULL,
                name TEXT NOT NULL,
                faculty TEXT NOT NULL DEFAULT 'ALL',
                is_private BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                used_at TIMESTAMP,
                used_by_guild_id INTEGER,
                used_by_user_id INTEGER,
                status TEXT DEFAULT 'ACTIVE'
            );

            -- 8. Society Registration Applications
            CREATE TABLE IF NOT EXISTS society_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                guild_name TEXT NOT NULL,
                applicant_id INTEGER NOT NULL,
                applicant_name TEXT NOT NULL,
                society_code TEXT NOT NULL,
                name TEXT NOT NULL,
                faculty TEXT NOT NULL DEFAULT 'ALL',
                description TEXT,
                is_private BOOLEAN DEFAULT 0,
                status TEXT DEFAULT 'PENDING',
                review_channel_id INTEGER,
                review_message_id INTEGER,
                reviewed_by INTEGER,
                review_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP
            );

            -- 9. Authorized console backend administrators
            CREATE TABLE IF NOT EXISTS console_admins (
                username TEXT PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'admin',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                added_by TEXT
            );

            -- 10. Term handover requests awaiting a second committee confirmation
            CREATE TABLE IF NOT EXISTS term_handover_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                society_id INTEGER NOT NULL REFERENCES societies(id),
                current_term_id INTEGER NOT NULL REFERENCES terms(id),
                next_academic_year TEXT NOT NULL,
                handover_notes TEXT,
                proposed_by INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                resolved_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_handover_one_pending
                ON term_handover_requests(society_id) WHERE status = 'PENDING';
            """
        )
        # Ensure is_private column exists in societies
        cursor = await self.conn.execute("PRAGMA table_info(societies);")
        existing_soc_cols = {row[1] for row in await cursor.fetchall()}
        if "is_private" not in existing_soc_cols:
            await self.conn.execute(
                "ALTER TABLE societies ADD COLUMN is_private BOOLEAN DEFAULT 0;"
            )

        # Ensure template positions include General Member for any existing societies
        cursor = await self.conn.execute("SELECT id FROM societies;")
        soc_rows = await cursor.fetchall()
        for s_row in soc_rows:
            s_id = s_row[0]
            chk = await self.conn.execute(
                "SELECT id FROM positions WHERE society_id = ? AND title = 'General Member';",
                (s_id,),
            )
            if not await chk.fetchone():
                await self.conn.execute(
                    """
                    INSERT INTO positions (
                        society_id, title, department, hierarchy_order, rbac_flags, is_template
                    ) VALUES (?, 'General Member', 'MEMBERSHIP', 15, ?, 1);
                    """,
                    (s_id, SocietyPermission.MEMBER_CORE.value),
                )

        await self.conn.commit()
        logger.info("TARClub database initialized with WAL mode and schema integrity.")

    async def register_society(
        self,
        guild_id: int,
        society_code: str,
        name: str,
        faculty: str,
        initial_term: str = "2025/2026",
        is_private: bool = False,
    ) -> int:
        """Register a new society, provision its initial theme, term, and 15-role template."""
        cursor = await self.conn.execute(
            """
            INSERT INTO societies (guild_id, society_code, name, faculty, is_private)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id;
            """,
            (
                guild_id,
                society_code.upper().strip(),
                name.strip(),
                faculty.upper().strip(),
                1 if is_private else 0,
            ),
        )
        row = await cursor.fetchone()
        society_id = row[0]

        # 1. Provision default theme
        await self.conn.execute(
            "INSERT INTO society_themes (society_id) VALUES (?);", (society_id,)
        )

        # 2. Provision initial active term
        term_cursor = await self.conn.execute(
            """
            INSERT INTO terms (society_id, term_name, academic_year, is_active)
            VALUES (?, ?, ?, 1)
            RETURNING id;
            """,
            (society_id, f"Term {initial_term}", initial_term),
        )
        await term_cursor.fetchone()

        # 3. Seed standard 14-role template positions
        for pos in DEFAULT_TEMPLATE_POSITIONS:
            await self.conn.execute(
                """
                INSERT INTO positions (
                    society_id, title, department, hierarchy_order, rbac_flags, is_template
                ) VALUES (?, ?, ?, ?, ?, 1);
                """,
                (society_id, pos["title"], pos["department"], pos["hierarchy"], pos["rbac"]),
            )

        await self.conn.commit()
        return society_id

    async def get_society(self, guild_id: int) -> dict[str, Any] | None:
        """Fetch society information for a Discord guild."""
        cursor = await self.conn.execute(
            """
            SELECT s.*, t.primary_color, t.secondary_color, t.emoji_badge, t.banner_url
            FROM societies s
            LEFT JOIN society_themes t ON s.id = t.society_id
            WHERE s.guild_id = ?;
            """,
            (guild_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_positions(self, society_id: int) -> list[dict[str, Any]]:
        """Fetch all positions for a society ordered by hierarchy."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM positions
            WHERE society_id = ?
            ORDER BY hierarchy_order ASC;
            """,
            (society_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_active_term(self, society_id: int) -> dict[str, Any] | None:
        """Fetch active annual term for a society."""
        cursor = await self.conn.execute(
            "SELECT * FROM terms WHERE society_id = ? AND is_active = 1;",
            (society_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_active_roster(self, society_id: int) -> list[dict[str, Any]]:
        """Fetch full committee roster for the currently active term."""
        cursor = await self.conn.execute(
            """
            SELECT p.id as position_id, p.title, p.department, p.hierarchy_order,
                   p.discord_role_id, p.rbac_flags,
                   cm.user_id, cm.student_id, cm.is_verified_student, cm.appointed_at
            FROM positions p
            JOIN terms t ON t.society_id = p.society_id AND t.is_active = 1
            LEFT JOIN committee_members cm
                ON cm.position_id = p.id AND cm.term_id = t.id AND cm.relieved_at IS NULL
            WHERE p.society_id = ?
            ORDER BY p.hierarchy_order ASC;
            """,
            (society_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def assign_committee_member(
        self,
        term_id: int,
        position_id: int,
        user_id: int,
        student_id: str | None = None,
        is_verified: bool = False,
    ) -> None:
        """Appoint a member to a position for an active term."""
        await self.conn.execute(
            """
            INSERT INTO committee_members (
                term_id, position_id, user_id, student_id, is_verified_student
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(term_id, position_id, user_id) DO UPDATE SET
                relieved_at = NULL,
                student_id = excluded.student_id,
                is_verified_student = excluded.is_verified_student;
            """,
            (term_id, position_id, user_id, student_id, 1 if is_verified else 0),
        )
        await self.conn.commit()

    async def relieve_committee_member(self, term_id: int, position_id: int, user_id: int) -> bool:
        """Relieve a committee member from their position."""
        cursor = await self.conn.execute(
            """
            UPDATE committee_members
            SET relieved_at = CURRENT_TIMESTAMP
            WHERE term_id = ? AND position_id = ? AND user_id = ? AND relieved_at IS NULL;
            """,
            (term_id, position_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def get_user_permissions(self, society_id: int, user_id: int) -> int:
        """Resolve bitwise aggregated RBAC permissions for a user in the active term."""
        cursor = await self.conn.execute(
            """
            SELECT p.rbac_flags, cm.is_verified_student
            FROM committee_members cm
            JOIN terms t ON cm.term_id = t.id AND t.is_active = 1
            JOIN positions p ON cm.position_id = p.id
            WHERE t.society_id = ? AND cm.user_id = ? AND cm.relieved_at IS NULL;
            """,
            (society_id, user_id),
        )
        rows = await cursor.fetchall()
        total = 0
        for r in rows:
            flags = r["rbac_flags"]
            if not r["is_verified_student"]:
                # Strip out verified-only privileges (e.g. voting, exclusive events)
                flags &= ~SocietyPermission.VOTE_ELECTION.value
                flags &= ~SocietyPermission.PARTICIPATE_EVENTS.value
            total |= flags
        return total

    async def update_theme(
        self,
        society_id: int,
        primary_color: str | None = None,
        secondary_color: str | None = None,
        banner_url: str | None = None,
        logo_url: str | None = None,
        emoji_badge: str | None = None,
        footer_text: str | None = None,
    ) -> None:
        """Update branding and dynamic theming attributes."""
        updates: list[str] = []
        params: list[Any] = []

        if primary_color is not None:
            updates.append("primary_color = ?")
            params.append(primary_color)
        if secondary_color is not None:
            updates.append("secondary_color = ?")
            params.append(secondary_color)
        if banner_url is not None:
            updates.append("banner_url = ?")
            params.append(banner_url)
        if logo_url is not None:
            updates.append("logo_url = ?")
            params.append(logo_url)
        if emoji_badge is not None:
            updates.append("emoji_badge = ?")
            params.append(emoji_badge)
        if footer_text is not None:
            updates.append("footer_text = ?")
            params.append(footer_text)

        if not updates:
            return

        params.append(society_id)
        query = f"UPDATE society_themes SET {', '.join(updates)} WHERE society_id = ?;"
        await self.conn.execute(query, tuple(params))
        await self.conn.commit()

    async def add_custom_position(
        self,
        society_id: int,
        title: str,
        department: str,
        hierarchy_order: int,
        rbac_flags: int = 0,
    ) -> int:
        """Add a new custom position to the society template."""
        cursor = await self.conn.execute(
            """
            INSERT INTO positions (
                society_id, title, department, hierarchy_order, rbac_flags, is_template
            ) VALUES (?, ?, ?, ?, ?, 0)
            RETURNING id;
            """,
            (society_id, title.strip(), department.upper().strip(), hierarchy_order, rbac_flags),
        )
        row = await cursor.fetchone()
        await self.conn.commit()
        return row[0]

    async def update_position(
        self,
        position_id: int,
        new_title: str | None = None,
        department: str | None = None,
        hierarchy_order: int | None = None,
        rbac_flags: int | None = None,
    ) -> None:
        """Modify an existing position."""
        updates: list[str] = []
        params: list[Any] = []

        if new_title is not None:
            updates.append("title = ?")
            params.append(new_title.strip())
        if department is not None:
            updates.append("department = ?")
            params.append(department.upper().strip())
        if hierarchy_order is not None:
            updates.append("hierarchy_order = ?")
            params.append(hierarchy_order)
        if rbac_flags is not None:
            updates.append("rbac_flags = ?")
            params.append(rbac_flags)

        if not updates:
            return

        params.append(position_id)
        query = f"UPDATE positions SET {', '.join(updates)} WHERE id = ?;"
        await self.conn.execute(query, tuple(params))
        await self.conn.commit()

    async def handover_term(
        self,
        society_id: int,
        current_term_id: int,
        next_academic_year: str,
        handover_notes: str | None = None,
    ) -> int:
        """Conclude active term, archive tenure, and activate the incoming term."""
        # 1. Archive current term
        await self.conn.execute(
            """
            UPDATE terms
            SET is_active = 0,
                end_date = CURRENT_TIMESTAMP,
                handover_at = CURRENT_TIMESTAMP,
                handover_notes = ?
            WHERE id = ?;
            """,
            (handover_notes, current_term_id),
        )

        # 2. Activate incoming term
        next_term_name = f"Term {next_academic_year}"
        cursor = await self.conn.execute(
            """
            INSERT INTO terms (society_id, term_name, academic_year, is_active)
            VALUES (?, ?, ?, 1)
            RETURNING id;
            """,
            (society_id, next_term_name, next_academic_year),
        )
        row = await cursor.fetchone()
        await self.conn.commit()
        return row[0]

    async def get_pending_handover(self, society_id: int) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM term_handover_requests WHERE society_id = ? AND status = 'PENDING';",
            (society_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def propose_handover(
        self,
        society_id: int,
        current_term_id: int,
        next_academic_year: str,
        handover_notes: str | None,
        proposed_by: int,
    ) -> int:
        """Open a pending handover. Raises sqlite3.IntegrityError if one is already pending."""
        try:
            cursor = await self.conn.execute(
                """
                INSERT INTO term_handover_requests
                    (society_id, current_term_id, next_academic_year, handover_notes, proposed_by)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id;
                """,
                (society_id, current_term_id, next_academic_year, handover_notes, proposed_by),
            )
            row = await cursor.fetchone()
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
        return row[0]

    async def confirm_handover(self, society_id: int, confirmed_by: int) -> int | None:
        """Confirm the pending handover as a second person and execute it.

        Returns the new term id, or None when nothing is pending or the proposer
        is confirming their own request.
        """
        try:
            cursor = await self.conn.execute(
                """
                UPDATE term_handover_requests
                SET status = 'CONFIRMED', resolved_by = ?, resolved_at = CURRENT_TIMESTAMP
                WHERE society_id = ? AND status = 'PENDING' AND proposed_by != ?
                RETURNING current_term_id, next_academic_year, handover_notes;
                """,
                (confirmed_by, society_id, confirmed_by),
            )
            row = await cursor.fetchone()
            if row is None:
                await self.conn.rollback()
                return None
            return await self.handover_term(
                society_id=society_id,
                current_term_id=row["current_term_id"],
                next_academic_year=row["next_academic_year"],
                handover_notes=row["handover_notes"],
            )
        except Exception:
            await self.conn.rollback()
            raise

    async def cancel_handover(self, society_id: int, cancelled_by: int) -> bool:
        cursor = await self.conn.execute(
            """
            UPDATE term_handover_requests
            SET status = 'CANCELLED', resolved_by = ?, resolved_at = CURRENT_TIMESTAMP
            WHERE society_id = ? AND status = 'PENDING';
            """,
            (cancelled_by, society_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def get_term_history(self, society_id: int) -> list[dict[str, Any]]:
        """Fetch all terms past and present for a society."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM terms
            WHERE society_id = ?
            ORDER BY id DESC;
            """,
            (society_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_integration(self, guild_id: int) -> dict[str, Any] | None:
        """Fetch TARVeri cross-bot integration state for a guild."""
        cursor = await self.conn.execute(
            "SELECT * FROM tarveri_integrations WHERE guild_id = ?;",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def set_integration(
        self,
        guild_id: int,
        is_linked: bool,
        enforce_student: bool = True,
        shared_secret_hash: str | None = None,
    ) -> None:
        """Update or insert integration status for a guild."""
        await self.conn.execute(
            """
            INSERT INTO tarveri_integrations (
                guild_id, is_linked, paired_at, shared_secret_hash, enforce_student_verification
            ) VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                is_linked = excluded.is_linked,
                paired_at = CURRENT_TIMESTAMP,
                shared_secret_hash = coalesce(
                    excluded.shared_secret_hash,
                    tarveri_integrations.shared_secret_hash
                ),
                enforce_student_verification = excluded.enforce_student_verification;
            """,
            (guild_id, 1 if is_linked else 0, shared_secret_hash, 1 if enforce_student else 0),
        )
        await self.conn.commit()

    async def set_society_privacy(self, guild_id: int, is_private: bool) -> bool:
        """Toggle public/private visibility status of a society."""
        cursor = await self.conn.execute(
            "UPDATE societies SET is_private = ? WHERE guild_id = ?;",
            (1 if is_private else 0, guild_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def list_public_societies(self) -> list[dict[str, Any]]:
        """Fetch all public societies for directory discovery."""
        cursor = await self.conn.execute(
            """
            SELECT s.*, t.primary_color, t.emoji_badge
            FROM societies s
            LEFT JOIN society_themes t ON s.id = t.society_id
            WHERE s.is_private = 0
            ORDER BY s.faculty ASC, s.name ASC;
            """
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ==========================================
    # 🎟️ One-Time Tokens & Applications
    # ==========================================

    async def create_society_token(
        self,
        society_code: str,
        name: str,
        faculty: str = "ALL",
        is_private: bool = False,
        days_valid: int = 30,
    ) -> str:
        """Generates a secure, human-readable registration token (e.g. TC-4F8A-9C12)."""
        random_hex = secrets.token_hex(4).upper()
        token = f"TC-{random_hex[:4]}-{random_hex[4:]}"
        expires_at = (datetime.now(UTC) + timedelta(days=days_valid)).strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO society_tokens (token, society_code, name, faculty, is_private, expires_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                token,
                society_code.upper(),
                name,
                faculty.upper(),
                1 if is_private else 0,
                expires_at,
            ),
        )
        await self.conn.commit()
        return token

    async def get_society_token(self, token: str) -> dict[str, Any] | None:
        """Retrieves details of a registration token."""
        cursor = await self.conn.execute(
            "SELECT * FROM society_tokens WHERE token = ?;", (token.strip().upper(),)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def redeem_society_token(self, token: str, guild_id: int, user_id: int) -> bool:
        """Marks a token as redeemed by a specific guild and user."""
        cursor = await self.conn.execute(
            """
            UPDATE society_tokens
            SET status = 'USED', used_at = CURRENT_TIMESTAMP,
                used_by_guild_id = ?, used_by_user_id = ?
            WHERE token = ? AND status = 'ACTIVE';
            """,
            (guild_id, user_id, token.strip().upper()),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def revoke_society_token(self, token: str) -> bool:
        """Revokes an unused token."""
        cursor = await self.conn.execute(
            "UPDATE society_tokens SET status = 'REVOKED' WHERE token = ? AND status = 'ACTIVE';",
            (token.strip().upper(),),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def list_society_tokens(self, status: str | None = None) -> list[dict[str, Any]]:
        """Lists tokens, optionally filtered by status."""
        if status:
            cursor = await self.conn.execute(
                "SELECT * FROM society_tokens WHERE status = ? ORDER BY id DESC;",
                (status.upper(),),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT * FROM society_tokens ORDER BY id DESC LIMIT 50;"
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def create_society_application(
        self,
        guild_id: int,
        guild_name: str,
        applicant_id: int,
        applicant_name: str,
        society_code: str,
        name: str,
        faculty: str = "ALL",
        description: str = "",
        is_private: bool = False,
    ) -> int:
        """Creates a pending society registration application."""
        cursor = await self.conn.execute(
            """
            INSERT INTO society_applications (
                guild_id, guild_name, applicant_id, applicant_name,
                society_code, name, faculty, description, is_private, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING');
            """,
            (
                guild_id,
                guild_name,
                applicant_id,
                applicant_name,
                society_code.upper(),
                name,
                faculty.upper(),
                description,
                1 if is_private else 0,
            ),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def get_society_application(self, app_id: int) -> dict[str, Any] | None:
        """Retrieves a single application by ID."""
        cursor = await self.conn.execute(
            "SELECT * FROM society_applications WHERE id = ?;", (app_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_society_applications(self, status: str = "PENDING") -> list[dict[str, Any]]:
        """Lists applications by status."""
        cursor = await self.conn.execute(
            "SELECT * FROM society_applications WHERE status = ? ORDER BY id ASC;",
            (status.upper(),),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def resolve_society_application(
        self, app_id: int, status: str, reviewer_id: int, reason: str = ""
    ) -> bool:
        """Resolves an application as APPROVED or REJECTED."""
        cursor = await self.conn.execute(
            """
            UPDATE society_applications
            SET status = ?, reviewed_by = ?, review_reason = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'PENDING';
            """,
            (status.upper(), reviewer_id, reason, app_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # 🔐 Console Backend Admin Accounts
    # =========================================================================

    async def get_console_admins(self) -> list[dict[str, Any]]:
        """Returns all authorized console backend administrator usernames."""
        cursor = await self.conn.execute(
            "SELECT username, role, added_at, added_by FROM console_admins ORDER BY added_at ASC;"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def is_console_admin(self, username: str) -> bool:
        """Checks if a username is in the authorized console administrators table."""
        cursor = await self.conn.execute(
            "SELECT 1 FROM console_admins WHERE LOWER(username) = LOWER(?);",
            (username.strip(),),
        )
        return await cursor.fetchone() is not None

    async def has_any_console_admin(self) -> bool:
        """Returns True if at least one console administrator account exists."""
        cursor = await self.conn.execute("SELECT 1 FROM console_admins LIMIT 1;")
        return await cursor.fetchone() is not None

    async def add_console_admin(
        self, username: str, role: str = "admin", added_by: str | None = None
    ) -> bool:
        """Adds a username to the authorized console administrators table."""
        try:
            await self.conn.execute(
                """INSERT INTO console_admins (username, role, added_by)
                   VALUES (?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET role = excluded.role;""",
                (username.strip(), role, added_by),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to add console admin '%s': %s", username, e)
            return False

    async def remove_console_admin(self, username: str) -> bool:
        """Removes a username from the authorized console administrators table."""
        try:
            cursor = await self.conn.execute(
                "DELETE FROM console_admins WHERE LOWER(username) = LOWER(?);",
                (username.strip(),),
            )
            await self.conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error("Failed to remove console admin '%s': %s", username, e)
            return False
