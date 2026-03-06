"""Tests for configuration."""

import os
import pytest
from unittest.mock import patch

from src.config import AgentConfig


class TestAgentConfigFromEnv:
    """Test AgentConfig.from_env."""

    @patch.dict(os.environ, {"SYNC_INTERVAL_SECONDS": "120", "ARTWORK_FOLDER": "custom_artworks"})
    def test_loads_values_from_env(self):
        """Loads configuration from environment variables."""
        config = AgentConfig.from_env()
        assert config.SYNC_INTERVAL_SECONDS == 120
        assert config.ARTWORK_FOLDER == "custom_artworks"

    @patch.dict(os.environ, {}, clear=True)
    def test_uses_defaults_when_env_empty(self):
        """Uses default values when environment variables are not set."""
        config = AgentConfig.from_env()
        assert config.SYNC_INTERVAL_SECONDS == 60
        assert config.ARTWORK_FOLDER == "artworks"
        assert config.USERS_FOLDER == "users"
        assert config.API_BASE_URL == "http://localhost:3000"


class TestAgentConfigPostInit:
    """Test AgentConfig __post_init__."""

    def test_sets_log_dirs_when_empty(self):
        """Sets LOG_DIR, RENAME_LOG_DIR, MOVEMENT_LOG_DIR when empty."""
        config = AgentConfig(LOG_DIR="", RENAME_LOG_DIR="", MOVEMENT_LOG_DIR="")
        assert config.LOG_DIR != ""
        assert "logs" in config.LOG_DIR
        assert "renames" in config.RENAME_LOG_DIR
        assert "movements" in config.MOVEMENT_LOG_DIR
        assert config.RENAME_LOG_DIR.startswith(config.LOG_DIR)
        assert config.MOVEMENT_LOG_DIR.startswith(config.LOG_DIR)

    def test_preserves_explicit_log_dir(self):
        """Preserves LOG_DIR when explicitly provided."""
        config = AgentConfig(LOG_DIR="/custom/logs")
        assert config.LOG_DIR == "/custom/logs"
