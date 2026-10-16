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
    shared_secret: str
    log_level: str
    environment: str

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

        return cls(
            discord_token=token,
            application_id=application_id,
            database_path=db_path,
            tarveri_bot_id=tarveri_bot_id,
            enable_tarveri_integration=enable_integration,
            shared_secret=os.getenv("TARCLUB_SHARED_SECRET", ""),
            log_level=os.getenv("TARCLUB_LOG_LEVEL", "INFO").upper(),
            environment=os.getenv("TARCLUB_ENVIRONMENT", "development").lower(),
        )
