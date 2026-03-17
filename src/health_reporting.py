"""Structured health/status event reporting for print-agent."""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

EVENT_SCHEMA = "print-agent-status.v1"

_reporter_lock = threading.Lock()
_stdout_lock = threading.Lock()
_reporter: Optional["StatusReporter"] = None


def _iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class StatusReporter:
    """Emit single-line JSON health events to stdout."""

    def __init__(
        self,
        enabled: bool,
        service_name: str,
        instance_id: Optional[str] = None,
        heartbeat_interval_seconds: int = 30,
    ) -> None:
        self.enabled = enabled
        self.service_name = service_name
        self.instance_id = instance_id
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._last_heartbeat_at = 0.0

    def emit(
        self,
        event: str,
        *,
        level: str = "info",
        state: Optional[str] = None,
        healthy: Optional[bool] = None,
        details: Optional[Dict[str, Any]] = None,
        service_name: Optional[str] = None,
        instance_id: Optional[str] = None,
    ) -> None:
        """Emit a structured event to stdout."""
        if not self.enabled:
            return

        payload: Dict[str, Any] = {
            "schema": EVENT_SCHEMA,
            "ts": _iso_now(),
            "service": service_name or self.service_name,
            "instance_id": instance_id if instance_id is not None else self.instance_id,
            "pid": os.getpid(),
            "event": event,
            "level": level,
        }
        if state is not None:
            payload["state"] = state
        if healthy is not None:
            payload["healthy"] = healthy
        if details:
            payload["details"] = details

        line = json.dumps(payload, separators=(",", ":"), default=str)
        with _stdout_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def emit_heartbeat(
        self,
        *,
        state: str,
        healthy: bool,
        details: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> bool:
        """Emit a heartbeat if the interval has elapsed."""
        now = time.time()
        if not force and now - self._last_heartbeat_at < self.heartbeat_interval_seconds:
            return False
        self._last_heartbeat_at = now
        self.emit(
            "heartbeat",
            level="info" if healthy else "warning",
            state=state,
            healthy=healthy,
            details=details,
        )
        return True


def configure_status_reporting(
    enabled: bool,
    service_name: str,
    instance_id: Optional[str] = None,
    heartbeat_interval_seconds: int = 30,
) -> StatusReporter:
    """Configure the process-global status reporter."""
    global _reporter
    reporter = StatusReporter(
        enabled=enabled,
        service_name=service_name,
        instance_id=instance_id,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    with _reporter_lock:
        _reporter = reporter
    return reporter


def get_status_reporter() -> Optional[StatusReporter]:
    """Return the configured status reporter."""
    with _reporter_lock:
        return _reporter


def emit_status_event(
    event: str,
    *,
    level: str = "info",
    state: Optional[str] = None,
    healthy: Optional[bool] = None,
    details: Optional[Dict[str, Any]] = None,
    service_name: Optional[str] = None,
    instance_id: Optional[str] = None,
    force: bool = False,
) -> None:
    """Emit a structured status event using the configured reporter."""
    reporter = get_status_reporter()
    if reporter is not None:
        reporter.emit(
            event,
            level=level,
            state=state,
            healthy=healthy,
            details=details,
            service_name=service_name,
            instance_id=instance_id,
        )
        return

    if not force:
        return

    StatusReporter(
        enabled=True,
        service_name=service_name or "PrintAgentSync",
        instance_id=instance_id,
    ).emit(
        event,
        level=level,
        state=state,
        healthy=healthy,
        details=details,
        service_name=service_name,
        instance_id=instance_id,
    )


def emit_status_heartbeat(
    *,
    state: str,
    healthy: bool,
    details: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> bool:
    """Emit a heartbeat through the configured reporter."""
    reporter = get_status_reporter()
    if reporter is None:
        return False
    return reporter.emit_heartbeat(
        state=state,
        healthy=healthy,
        details=details,
        force=force,
    )
