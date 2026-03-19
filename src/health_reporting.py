"""Structured health/status event reporting for print-agent."""

import json
import logging
import os
import sys
import threading
import time
import socket
import requests
from datetime import datetime, timezone
from typing import Any, Dict, Optional

EVENT_SCHEMA = "print-agent-status.v1"

_reporter_lock = threading.Lock()
_stdout_lock = threading.Lock()
_reporter: Optional["StatusReporter"] = None


def _iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class PostHogHandler(logging.Handler):
    """Logging handler that sends log records to PostHog."""

    def __init__(
        self,
        posthog_api_key: str,
        posthog_host: str,
        organisation_id: str,
        instance_id: Optional[str],
        machine_name: str,
    ):
        super().__init__()
        self.posthog_api_key = posthog_api_key
        self.posthog_host = posthog_host
        self.organisation_id = organisation_id
        self.instance_id = instance_id
        self.machine_name = machine_name

    def emit(self, record: logging.LogRecord) -> None:
        """Send log record to PostHog."""
        try:
            category = getattr(record, 'category', 'service')
            level = record.levelname.lower()

            event_name = category
            message = self.format(record)

            state = getattr(record, 'state', None)
            healthy = getattr(record, 'healthy', None)
            details = getattr(record, 'details', None)
            pathname = getattr(record, 'pathname', record.pathname)

            posthog_payload = {
                "token": self.posthog_api_key,
                "event": event_name,
                "properties": {
                    "distinct_id": self.machine_name,
                    "organisation_id": self.organisation_id,
                    "instance_id": self.instance_id,
                    "pid": os.getpid(),
                    "message": message,
                    "category": category,
                    "level": level,
                    "pathname": pathname,
                    "service": "agent",
                },
                "timestamp": _iso_now(),
            }

            if state:
                posthog_payload["properties"]["state"] = state
            if healthy is not None:
                posthog_payload["properties"]["healthy"] = healthy
            if details:
                posthog_payload["properties"]["details"] = details

            requests.post(
                f"{self.posthog_host}/i/v0/e/",
                json=posthog_payload,
                timeout=1.0,
            )
        except Exception:
            pass


class StatusReporter:
    """Emit single-line JSON health events to stdout."""

    def __init__(
        self,
        enabled: bool,
        service_name: str,
        instance_id: Optional[str] = None,
        heartbeat_interval_seconds: int = 30,
        posthog_enabled: bool = False,
        posthog_api_key: str = "",
        posthog_host: str = "",
        organisation_id: str = "",
    ) -> None:
        self.enabled = enabled
        self.service_name = service_name
        self.instance_id = instance_id
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._last_heartbeat_at = 0.0
        self.posthog_enabled = posthog_enabled
        self.posthog_api_key = posthog_api_key
        self.posthog_host = posthog_host
        self.organisation_id = organisation_id
        self.machine_name = socket.gethostname()

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
        pathname: Optional[str] = None,
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

        if self.posthog_enabled and self.posthog_api_key and self.posthog_host:
            posthog_payload = {
                "token": self.posthog_api_key,
                "event": event,
                "properties": {
                    "distinct_id": self.machine_name,
                    "organisation_id": self.organisation_id,
                    "instance_id": self.instance_id,
                    "pid": os.getpid(),
                    "pathname": pathname,
                    **payload,  # Include all existing status event details
                    "service": "agent",
                },
                "timestamp": _iso_now(),
            }
            try:
                requests.post(
                    f"{self.posthog_host}/i/v0/e/",
                    json=posthog_payload,
                    timeout=1.0,  # Short timeout to avoid blocking
                )
            except requests.exceptions.RequestException as e:
                # Log the error but don't re-raise, to avoid blocking the main service
                with _stdout_lock:
                    sys.stderr.write(f"Error sending PostHog event: {e}\n")
                    sys.stderr.flush()

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
    posthog_enabled: bool = False,
    posthog_api_key: str = "",
    posthog_host: str = "",
    organisation_id: str = "",
) -> StatusReporter:
    """Configure the process-global status reporter."""
    global _reporter
    reporter = StatusReporter(
        enabled=enabled,
        service_name=service_name,
        instance_id=instance_id,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        posthog_enabled=posthog_enabled,
        posthog_api_key=posthog_api_key,
        posthog_host=posthog_host,
        organisation_id=organisation_id,
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
    pathname: Optional[str] = None,
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
            pathname=pathname,
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
