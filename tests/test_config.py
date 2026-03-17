"""Tests for configuration."""

import json
import os
import pytest
from unittest.mock import patch

from src.config import AgentConfig


class TestAgentConfigFromFile:
    """Test AgentConfig.from_file."""

    def test_loads_values_from_file(self, tmp_path):
        """Loads configuration from JSON file."""
        config_data = {
            "SYNC_INTERVAL_SECONDS": 120,
            "ARTWORK_FOLDER": "custom_artworks",
            "API_KEY": "test-key"
        }
        config_file = tmp_path / "config.json"
        with open(config_file, 'w') as f:
            json.dump(config_data, f)

        config = AgentConfig.from_file(config_file)
        assert config.SYNC_INTERVAL_SECONDS == 120
        assert config.ARTWORK_FOLDER == "custom_artworks"
        assert config.API_KEY == "test-key"

    def test_uses_defaults_for_missing_keys(self, tmp_path):
        """Uses default values for keys not present in config file."""
        config_data = {"API_KEY": "test-key"}
        config_file = tmp_path / "config.json"
        with open(config_file, 'w') as f:
            json.dump(config_data, f)

        config = AgentConfig.from_file(config_file)
        assert config.SYNC_INTERVAL_SECONDS == 60  # default
        assert config.ARTWORK_FOLDER == "artworks"  # default
        assert config.USERS_FOLDER == "users"  # default
        assert config.STRUCTURED_STATUS_STDOUT_ENABLED is False
        assert config.HEALTH_HEARTBEAT_INTERVAL_SECONDS == 30
        assert config.API_KEY == "test-key"  # from file

    def test_loads_health_reporting_settings(self, tmp_path):
        """Loads structured status reporting settings."""
        config_data = {
            "STRUCTURED_STATUS_STDOUT_ENABLED": True,
            "HEALTH_HEARTBEAT_INTERVAL_SECONDS": 45,
        }
        config_file = tmp_path / "config.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f)

        config = AgentConfig.from_file(config_file)

        assert config.STRUCTURED_STATUS_STDOUT_ENABLED is True
        assert config.HEALTH_HEARTBEAT_INTERVAL_SECONDS == 45

    def test_handles_invalid_json(self, tmp_path):
        """Raises JSONDecodeError for invalid JSON."""
        config_file = tmp_path / "config.json"
        with open(config_file, 'w') as f:
            f.write("invalid json {")

        with pytest.raises(json.JSONDecodeError):
            AgentConfig.from_file(config_file)

    def test_handles_missing_file(self, tmp_path):
        """Raises FileNotFoundError for missing config file."""
        config_file = tmp_path / "missing.json"

        with pytest.raises(FileNotFoundError):
            AgentConfig.from_file(config_file)


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
