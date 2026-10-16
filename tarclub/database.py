"""
TARClub Database façade coordinating domain schemas and repository mixins.
"""

from __future__ import annotations

import logging
from typing import Any

from tarclub.db.base import DatabaseBase

logger = logging.getLogger("tarclub.database")

# Standard 14-Role Starter Template
DEFAULT_TEMPLATE_POSITIONS = [
    {"title": "Chairperson", "department": "PRESIDIUM", "hierarchy": 1, "rbac": 0x0FFF},
    {"title": "Vice Chairperson", "department": "PRESIDIUM", "hierarchy": 2, "rbac": 0x07FF},
    {"title": "Deputy Chairperson", "department": "PRESIDIUM", "hierarchy": 3, "rbac": 0x07FF},
    {"title": "Deputy Vice Chairperson", "department": "PRESIDIUM", "hierarchy": 4, "rbac": 0x07FF},
    {"title": "Secretary", "department": "SECRETARIAT", "hierarchy": 5, "rbac": 0x051F},
    {"title": "Deputy Secretary", "department": "SECRETARIAT", "hierarchy": 6, "rbac": 0x0513},
    {"title": "Treasurer", "department": "FINANCE", "hierarchy": 7, "rbac": 0x05D0},
    {"title": "Deputy Treasurer", "department": "FINANCE", "hierarchy": 8, "rbac": 0x0550},
    {
        "title": "Programme & Activities Director",
        "department": "DIRECTORSHIP",
        "hierarchy": 9,
        "rbac": 0x0030,
    },
    {
        "title": "Technical Resource Director",
        "department": "DIRECTORSHIP",
        "hierarchy": 10,
        "rbac": 0x0210,
    },
    {"title": "Publicity Director", "department": "DIRECTORSHIP", "hierarchy": 11, "rbac": 0x0010},
    {"title": "Publicity Member", "department": "DIRECTORSHIP", "hierarchy": 12, "rbac": 0x0000},
    {
        "title": "Art and Promotion Director",
        "department": "DIRECTORSHIP",
        "hierarchy": 13,
        "rbac": 0x0010,
    },
    {"title": "Internal Auditor", "department": "DIRECTORSHIP", "hierarchy": 14, "rbac": 0x0100},
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
            """
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
    ) -> int:
        """Register a new society, provision its initial theme, term, and 14-role template."""
        cursor = await self.conn.execute(
            """
            INSERT INTO societies (guild_id, society_code, name, faculty)
            VALUES (?, ?, ?, ?)
            RETURNING id;
            """,
            (guild_id, society_code.upper().strip(), name.strip(), faculty.upper().strip()),
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
