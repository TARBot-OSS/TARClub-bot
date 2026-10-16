"""
Database connection base for TARClub.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger("tarclub.database")


class DatabaseBase:
    """Base database connection and transaction manager."""

    def __init__(self, db_path: Path | str = "tarclub.db") -> None:
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Initialize connection with performance and integrity PRAGMAs."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row

        # Performance and data integrity PRAGMAs
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self._conn.execute("PRAGMA synchronous = NORMAL;")
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.execute("PRAGMA busy_timeout = 5000;")
        await self._conn.commit()

    async def close(self) -> None:
        """Close connection cleanly."""
        if self._conn:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database connection is not open. Call connect() first.")
        return self._conn
