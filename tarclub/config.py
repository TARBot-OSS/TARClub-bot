"""
Configuration loader for TARClub bot.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    discord_token: str
    application_id: int | None
    database_path: Path
    tarveri_bot_id: int
    enable_tarveri_integration: bool
    tarveri_db_path: Path | None
    shared_secret: str
    log_level: str
    environment: str
    enable_console: bool = True
    review_channel_id: int | None = None
    superadmin_ids: frozenset[int] = frozenset()

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("TARCLUB_DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "TARCLUB_DISCORD_TOKEN is missing. Please set it in your environment or .env file."
            )

        app_id_raw = os.getenv("TARCLUB_APPLICATION_ID", "").strip()
        application_id = int(app_id_raw) if app_id_raw.isdigit() else None

        db_path = Path(os.getenv("TARCLUB_DATABASE_PATH", "tarclub.db"))

        tarveri_id_raw = os.getenv("TARCLUB_TARVERI_BOT_ID", "1542166264561016963").strip()
        tarveri_bot_id = int(tarveri_id_raw) if tarveri_id_raw.isdigit() else 1542166264561016963

        enable_integration = os.getenv("TARCLUB_ENABLE_TARVERI_INTEGRATION", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        tarveri_db_raw = os.getenv("TARCLUB_TARVERI_DB_PATH", "").strip()
        tarveri_db_path = Path(tarveri_db_raw) if tarveri_db_raw else None

        enable_console = os.getenv("TARCLUB_ENABLE_CONSOLE", "true").lower() in ("true", "1", "yes")

        rev_raw = os.getenv("TARCLUB_REVIEW_CHANNEL_ID", "").strip()
        review_channel_id = int(rev_raw) if rev_raw.isdigit() else None

        admins_raw = os.getenv("TARCLUB_SUPERADMIN_IDS", "").strip()
        superadmin_ids = (
            frozenset(int(x.strip()) for x in admins_raw.split(",") if x.strip().isdigit())
            if admins_raw
            else frozenset()
        )

        return cls(
            discord_token=token,
            application_id=application_id,
            database_path=db_path,
            tarveri_bot_id=tarveri_bot_id,
            enable_tarveri_integration=enable_integration,
            tarveri_db_path=tarveri_db_path,
            shared_secret=os.getenv("TARCLUB_SHARED_SECRET", ""),
            log_level=os.getenv("TARCLUB_LOG_LEVEL", "INFO").upper(),
            environment=os.getenv("TARCLUB_ENVIRONMENT", "development").lower(),
            enable_console=enable_console,
            review_channel_id=review_channel_id,
            superadmin_ids=superadmin_ids,
        )
