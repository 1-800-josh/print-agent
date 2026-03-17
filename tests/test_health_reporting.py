"""Tests for structured health/status reporting."""

import io
import json
import sys
from unittest.mock import patch

from src.health_reporting import (
    configure_status_reporting,
    emit_status_event,
    emit_status_heartbeat,
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
