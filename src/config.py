"""Configuration management for Print Agent."""

import os
import platform
from dataclasses import dataclass
from typing import Optional


def get_default_data_dir() -> str:
    """Get platform-appropriate data directory."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("PROGRAMDATA", "C:\\ProgramData")
        return os.path.join(base, "PrintAgent")
    elif system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/PrintAgent")
    else:
        return os.environ.get("XDG_DATA_HOME", "/var/lib/printagent")


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

    # Paths
    OUTPUT_BASE_DIR: str = ""
    LOG_DIR: str = ""
    RENAME_LOG_DIR: str = ""
    MOVEMENT_LOG_DIR: str = ""
    HOT_FOLDERS_BASE: str = ""
    NETWORK_DRIVE_PREFIX: str = ""

    # Service
    SERVICE_NAME: str = "PrintAgentSync"

    def __post_init__(self) -> None:
        """Set default directories after initialization."""
        data_dir = get_default_data_dir()
        if not self.OUTPUT_BASE_DIR:
            self.OUTPUT_BASE_DIR = os.path.join(data_dir, "output")
        if not self.LOG_DIR:
            self.LOG_DIR = os.path.join(data_dir, "logs")
        if not self.RENAME_LOG_DIR:
            self.RENAME_LOG_DIR = os.path.join(data_dir, "logs", "renames")
        if not self.MOVEMENT_LOG_DIR:
            self.MOVEMENT_LOG_DIR = os.path.join(data_dir, "logs", "movements")
        if not self.HOT_FOLDERS_BASE:
            self.HOT_FOLDERS_BASE = os.path.join(data_dir, "hotfolders")

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
            OUTPUT_BASE_DIR=os.getenv("OUTPUT_BASE_DIR", ""),
            LOG_DIR=os.getenv("LOG_DIR", ""),
            RENAME_LOG_DIR=os.getenv("RENAME_LOG_DIR", ""),
            MOVEMENT_LOG_DIR=os.getenv("MOVEMENT_LOG_DIR", ""),
            HOT_FOLDERS_BASE=os.getenv("HOT_FOLDERS_BASE", ""),
            NETWORK_DRIVE_PREFIX=os.getenv("NETWORK_DRIVE_PREFIX", ""),
            SERVICE_NAME=os.getenv("SERVICE_NAME", cls.SERVICE_NAME),
        )
