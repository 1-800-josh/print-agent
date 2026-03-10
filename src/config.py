"""Configuration management for Print Agent."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass
class AgentConfig:
    """Configuration for Print Agent services."""

    # API Configuration
    API_BASE_URL: str = "http://localhost:3000"
    API_KEY: str = ""
    ORGANISATION_ID: str = ""
    UPLOADTHING_APP_ID: str = ""

    # Sync Configuration
    SYNC_INTERVAL_SECONDS: int = 60
    MAX_WORKERS: int = 10
    DOWNLOAD_TIMEOUT_SECONDS: int = 60
    CONFIG_REFRESH_INTERVAL_SECONDS: int = 300

    # File Watcher Configuration
    FILE_EVENT_DEBOUNCE_SECONDS: float = 2.0
    FILE_CLEANUP_INTERVAL_SECONDS: int = 30

    # Reconciliation
    RECONCILE_TASK_STATES: bool = True
    CLEANUP_EMPTY_ARTWORK_FOLDERS: bool = True

    # Paths
    NETWORK_DRIVE_PREFIX: str = ""
    ARTWORK_FOLDER: str = "artworks"
    USERS_FOLDER: str = "users"

    # Service
    SERVICE_NAME: str = "PrintAgentSync"

    # Log directories (set via __post_init__)
    LOG_DIR: str = ""
    RENAME_LOG_DIR: str = ""
    MOVEMENT_LOG_DIR: str = ""

    def __post_init__(self) -> None:
        """Set default directories after initialization."""
        if not self.LOG_DIR:
            self.LOG_DIR = os.path.join(os.getcwd(), "logs")
        if not self.RENAME_LOG_DIR:
            self.RENAME_LOG_DIR = os.path.join(self.LOG_DIR, "renames")
        if not self.MOVEMENT_LOG_DIR:
            self.MOVEMENT_LOG_DIR = os.path.join(self.LOG_DIR, "movements")

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Load configuration from environment variables."""
        return cls(
            API_BASE_URL=os.getenv("API_BASE_URL", cls.API_BASE_URL),
            API_KEY=os.getenv("API_KEY", cls.API_KEY),
            ORGANISATION_ID=os.getenv("ORGANISATION_ID", cls.ORGANISATION_ID),
            UPLOADTHING_APP_ID=os.getenv("UPLOADTHING_APP_ID", cls.UPLOADTHING_APP_ID),
            SYNC_INTERVAL_SECONDS=int(
                os.getenv("SYNC_INTERVAL_SECONDS", str(cls.SYNC_INTERVAL_SECONDS))
            ),
            MAX_WORKERS=int(os.getenv("MAX_WORKERS", str(cls.MAX_WORKERS))),
            DOWNLOAD_TIMEOUT_SECONDS=int(
                os.getenv("DOWNLOAD_TIMEOUT_SECONDS", str(cls.DOWNLOAD_TIMEOUT_SECONDS))
            ),
            CONFIG_REFRESH_INTERVAL_SECONDS=int(
                os.getenv(
                    "CONFIG_REFRESH_INTERVAL_SECONDS", str(cls.CONFIG_REFRESH_INTERVAL_SECONDS)
                )
            ),
            FILE_EVENT_DEBOUNCE_SECONDS=float(
                os.getenv("FILE_EVENT_DEBOUNCE_SECONDS", str(cls.FILE_EVENT_DEBOUNCE_SECONDS))
            ),
            FILE_CLEANUP_INTERVAL_SECONDS=int(
                os.getenv("FILE_CLEANUP_INTERVAL_SECONDS", str(cls.FILE_CLEANUP_INTERVAL_SECONDS))
            ),
            RECONCILE_TASK_STATES=os.getenv("RECONCILE_TASK_STATES", "true").lower()
            in ("true", "1", "yes"),
            CLEANUP_EMPTY_ARTWORK_FOLDERS=os.getenv("CLEANUP_EMPTY_ARTWORK_FOLDERS", "true").lower()
            in ("true", "1", "yes"),
            NETWORK_DRIVE_PREFIX=os.getenv("NETWORK_DRIVE_PREFIX", ""),
            ARTWORK_FOLDER=os.getenv("ARTWORK_FOLDER", cls.ARTWORK_FOLDER),
            USERS_FOLDER=os.getenv("USERS_FOLDER", cls.USERS_FOLDER),
            SERVICE_NAME=os.getenv("SERVICE_NAME", cls.SERVICE_NAME),
            LOG_DIR=os.getenv("LOG_DIR", ""),
            RENAME_LOG_DIR=os.getenv("RENAME_LOG_DIR", ""),
            MOVEMENT_LOG_DIR=os.getenv("MOVEMENT_LOG_DIR", ""),
        )

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "AgentConfig":
        """Load configuration from JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Type coercion helpers
        def to_bool(value: Union[str, bool, int]) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return value != 0
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)

        def to_int(value: Union[str, int]) -> int:
            return int(value) if isinstance(value, str) else value

        def to_float(value: Union[str, float]) -> float:
            return float(value) if isinstance(value, str) else value

        return cls(
            API_BASE_URL=data.get("API_BASE_URL", cls.API_BASE_URL),
            API_KEY=data.get("API_KEY", cls.API_KEY),
            ORGANISATION_ID=data.get("ORGANISATION_ID", cls.ORGANISATION_ID),
            UPLOADTHING_APP_ID=data.get("UPLOADTHING_APP_ID", cls.UPLOADTHING_APP_ID),
            SYNC_INTERVAL_SECONDS=to_int(data.get("SYNC_INTERVAL_SECONDS", cls.SYNC_INTERVAL_SECONDS)),
            MAX_WORKERS=to_int(data.get("MAX_WORKERS", cls.MAX_WORKERS)),
            DOWNLOAD_TIMEOUT_SECONDS=to_int(data.get("DOWNLOAD_TIMEOUT_SECONDS", cls.DOWNLOAD_TIMEOUT_SECONDS)),
            CONFIG_REFRESH_INTERVAL_SECONDS=to_int(data.get("CONFIG_REFRESH_INTERVAL_SECONDS", cls.CONFIG_REFRESH_INTERVAL_SECONDS)),
            FILE_EVENT_DEBOUNCE_SECONDS=to_float(data.get("FILE_EVENT_DEBOUNCE_SECONDS", cls.FILE_EVENT_DEBOUNCE_SECONDS)),
            FILE_CLEANUP_INTERVAL_SECONDS=to_int(data.get("FILE_CLEANUP_INTERVAL_SECONDS", cls.FILE_CLEANUP_INTERVAL_SECONDS)),
            RECONCILE_TASK_STATES=to_bool(data.get("RECONCILE_TASK_STATES", cls.RECONCILE_TASK_STATES)),
            CLEANUP_EMPTY_ARTWORK_FOLDERS=to_bool(data.get("CLEANUP_EMPTY_ARTWORK_FOLDERS", cls.CLEANUP_EMPTY_ARTWORK_FOLDERS)),
            NETWORK_DRIVE_PREFIX=data.get("NETWORK_DRIVE_PREFIX", cls.NETWORK_DRIVE_PREFIX),
            ARTWORK_FOLDER=data.get("ARTWORK_FOLDER", cls.ARTWORK_FOLDER),
            USERS_FOLDER=data.get("USERS_FOLDER", cls.USERS_FOLDER),
            SERVICE_NAME=data.get("SERVICE_NAME", cls.SERVICE_NAME),
            LOG_DIR=data.get("LOG_DIR", ""),
            RENAME_LOG_DIR=data.get("RENAME_LOG_DIR", ""),
            MOVEMENT_LOG_DIR=data.get("MOVEMENT_LOG_DIR", ""),
        )
