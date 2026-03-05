"""Configuration management for Print Agent."""

import os
from dataclasses import dataclass


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
            NETWORK_DRIVE_PREFIX=os.getenv("NETWORK_DRIVE_PREFIX", ""),
            ARTWORK_FOLDER=os.getenv("ARTWORK_FOLDER", cls.ARTWORK_FOLDER),
            USERS_FOLDER=os.getenv("USERS_FOLDER", cls.USERS_FOLDER),
            SERVICE_NAME=os.getenv("SERVICE_NAME", cls.SERVICE_NAME),
            LOG_DIR=os.getenv("LOG_DIR", ""),
            RENAME_LOG_DIR=os.getenv("RENAME_LOG_DIR", ""),
            MOVEMENT_LOG_DIR=os.getenv("MOVEMENT_LOG_DIR", ""),
        )
