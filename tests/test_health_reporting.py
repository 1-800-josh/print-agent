"""Tests for structured health/status reporting."""

import io
import json
import logging
import sys
from unittest.mock import patch, MagicMock
import requests

from src.health_reporting import (
    configure_status_reporting,
    emit_status_event,
    emit_status_heartbeat,
    StatusReporter,
    PostHogHandler
)

def get_mock_status_reporter(posthog_enabled: bool = False, posthog_api_key: str = "", posthog_host: str = "") -> StatusReporter:
    return configure_status_reporting(
        enabled=True,
        service_name="agent", # Updated to 'agent'
        instance_id="test-instance",
        heartbeat_interval_seconds=1,
        posthog_enabled=posthog_enabled,
        posthog_api_key=posthog_api_key,
        posthog_host=posthog_host,
        organisation_id="test-org-id",
    )


def test_emit_status_event_writes_json_line():
    stream = io.StringIO()
    with patch.object(sys, "stdout", stream):
        configure_status_reporting(
            enabled=True,
            service_name="PrintAgentSync",
            instance_id="agent-1",
            heartbeat_interval_seconds=30,
        )
        emit_status_event(
            "service_started",
            state="starting",
            healthy=True,
            details={"watcher_running": False},
        )

    payload = json.loads(stream.getvalue().strip())
    assert payload["schema"] == "print-agent-status.v1"
    assert payload["service"] == "PrintAgentSync"
    assert payload["instance_id"] == "agent-1"
    assert payload["event"] == "service_started"
    assert payload["state"] == "starting"
    assert payload["healthy"] is True
    assert payload["details"]["watcher_running"] is False

def test_emit_status_event_sends_posthog_event():
    with patch("src.health_reporting.requests.post") as mock_post:
        stream = io.StringIO()
        with patch.object(sys, "stdout", stream):
            reporter = get_mock_status_reporter(posthog_enabled=True, posthog_api_key="test-key", posthog_host="http://test-posthog.com")
            reporter.emit(
                "service_started",
                state="starting",
                healthy=True,
                details={"watcher_running": False},
            )
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://test-posthog.com/i/v0/e/"
        posthog_payload = kwargs["json"]
        assert posthog_payload["token"] == "test-key"
        assert posthog_payload["event"] == "service_started"
        assert "distinct_id" in posthog_payload["properties"]
        assert posthog_payload["properties"]["service"] == "agent"
        assert posthog_payload["properties"]["organisation_id"] == "test-org-id"
        assert posthog_payload["properties"]["instance_id"] == "test-instance"
        assert "pid" in posthog_payload["properties"]
        assert posthog_payload["properties"]["state"] == "starting"

def test_emit_status_event_posthog_error_does_not_block_stdout():
    with patch("src.health_reporting.requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.RequestException("Test PostHog error")
        stream = io.StringIO()
        with patch.object(sys, "stdout", stream):
            reporter = get_mock_status_reporter(posthog_enabled=True, posthog_api_key="test-key", posthog_host="http://test-posthog.com")
            reporter.emit(
                "service_started",
                state="starting",
                healthy=True,
                details={"watcher_running": False},
            )
        # Assert stdout still received the event
        payload = json.loads(stream.getvalue().strip())
        assert payload["event"] == "service_started"
        # Assert error was logged to stderr (though we don't capture stderr in this test setup)
        mock_post.assert_called_once()


def test_heartbeat_respects_interval_and_force():
    stream = io.StringIO()
    with patch.object(sys, "stdout", stream):
        configure_status_reporting(
            enabled=True,
            service_name="PrintAgentSync",
            heartbeat_interval_seconds=999,
        )
        assert emit_status_heartbeat(state="healthy", healthy=True, force=True) is True
        assert emit_status_heartbeat(state="healthy", healthy=True) is False

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "heartbeat"


def test_posthog_handler_sends_categorized_events():
    """Test PostHogHandler sends events with category as event name."""
    with patch("src.health_reporting.requests.post") as mock_post:
        handler = PostHogHandler(
            posthog_api_key="test-key",
            posthog_host="http://test.com",
            organisation_id="test-org",
            instance_id="test-instance",
            machine_name="test-machine",
        )
        
        test_logger = logging.getLogger("test_posthog_logger")
        test_logger.setLevel(logging.INFO)
        test_logger.handlers = []
        test_logger.addHandler(handler)
        
        # Test info log with category
        test_logger.info("Test message", extra={'category': 'sync', 'state': 'healthy'})
        
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]['json']
        assert payload['event'] == 'sync'
        assert 'Test message' in payload['properties']['message']
        assert payload['properties']['category'] == 'sync'
        assert payload['properties']['level'] == 'info'
        assert payload['properties']['state'] == 'healthy'
        assert payload['properties']['service'] == 'agent'


def test_posthog_handler_with_details():
    """Test PostHogHandler includes details in properties."""
    with patch("src.health_reporting.requests.post") as mock_post:
        handler = PostHogHandler(
            posthog_api_key="test-key",
            posthog_host="http://test.com",
            organisation_id="test-org",
            instance_id="test-instance",
            machine_name="test-machine",
        )
        
        test_logger = logging.getLogger("test_posthog_logger_2")
        test_logger.setLevel(logging.ERROR)
        test_logger.handlers = []
        test_logger.addHandler(handler)
        
        # Test error log with details
        test_logger.error(
            "API call failed",
            extra={
                'category': 'api',
                'state': 'degraded',
                'healthy': False,
                'details': {'operation': 'fetch_tasks', 'error': 'timeout'}
            }
        )
        
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]['json']
        assert payload['event'] == 'api'
        assert 'API call failed' in payload['properties']['message']
        assert payload['properties']['category'] == 'api'
        assert payload['properties']['level'] == 'error'
        assert payload['properties']['healthy'] is False
        assert payload['properties']['details']['operation'] == 'fetch_tasks'
