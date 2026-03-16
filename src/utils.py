"""Utility functions for Print Agent."""

import logging
import os
import signal
import sys
import threading
from datetime import datetime
from types import FrameType
from typing import Dict, List, Optional

# Windows Event Log imports (conditional)
try:
    import win32evtlog  # type: ignore
    import win32evtlogutil  # type: ignore
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

_shutdown_events: Dict[str, threading.Event] = {}


class WindowsEventLogHandler(logging.Handler):
    """Windows Event Log handler for logging to Windows Event Viewer."""

    def __init__(self, event_source: str, instance_id: Optional[str] = None):
        super().__init__()
        self.event_source = event_source
        self.instance_id = instance_id

        if not WIN32_AVAILABLE:
            raise ImportError("pywin32 is not available. Windows Event Log support disabled.")

    def emit(self, record):
        """Emit a record to the Windows Event Log."""
        try:
            # Map Python log levels to Windows Event Log types
            if record.levelno >= logging.ERROR:
                event_type = win32evtlog.EVENTLOG_ERROR_TYPE
            elif record.levelno >= logging.WARNING:
                event_type = win32evtlog.EVENTLOG_WARNING_TYPE
            else:
                event_type = win32evtlog.EVENTLOG_INFORMATION_TYPE

            # Format the message
            message = self.format(record)
            if self.instance_id:
                message = f"[{self.instance_id}] {message}"

            # Write to Windows Event Log
            win32evtlogutil.ReportEvent(
                self.event_source,
                1,
                eventCategory=0,
                eventType=event_type,
                strings=[message],
                sid=None,
            )
        except Exception:
            # Silently ignore errors to avoid logging loops
            pass


def get_timestamp() -> str:
    """Get current timestamp in YYYY-MM-DD_HH-MM-SS format."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def generate_filename(
    order_id: str,
    artwork_group_id: str,
    option_values: List[str],
    task_morse_code: Optional[str] = None,
    task_id: Optional[str] = None,
    artwork_index: int = 0,
) -> str:
    """Generate filename for an artwork.

    Filename format: {task_id}-{order_id}-{morse_code} for single images
    or {task_id}-{order_id}-{morse_code}_image_{n} for multiple images
    """
    if task_id:
        base_name = f"{task_id}-{order_id}"
        if task_morse_code:
            base_name = f"{base_name}-{task_morse_code}"
    else:
        base_name = task_morse_code if task_morse_code else f"{order_id}-{artwork_group_id}"

    if artwork_index > 0:
        base_name = f"{base_name}_image_{artwork_index}"

    return base_name


def ensure_unique_filename(directory: str, filename: str, extension: str = ".png") -> str:
    """Ensure filename is unique by appending counter if needed."""
    base_path = os.path.join(directory, f"{filename}{extension}")
    if not os.path.exists(base_path):
        return base_path

    counter = 1
    while True:
        new_filename = f"{filename}-{counter}{extension}"
        new_path = os.path.join(directory, new_filename)
        if not os.path.exists(new_path):
            return new_path
        counter += 1


def setup_logging(
    name: str,
    log_dir: str,
    level: int = logging.INFO,
    instance_id: Optional[str] = None,
    force_event_log: bool = False,
    event_source: Optional[str] = None
) -> logging.Logger:
    """Set up logging with file, console, and Windows Event Log handlers."""
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        logger.handlers.clear()

    # Create unique filename for each instance
    timestamp = get_timestamp()
    instance_suffix = f"_{instance_id}" if instance_id else ""
    log_file = os.path.join(log_dir, f"{name}_{timestamp}{instance_suffix}.log")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info("Logging to file: %s", log_file)

    # Add Windows Event Log handler if explicitly enabled for this run mode.
    if sys.platform == "win32" and force_event_log:
        try:
            event_log_handler = WindowsEventLogHandler(event_source or name, instance_id)
            event_log_handler.setFormatter(formatter)
            logger.addHandler(event_log_handler)
        except ImportError:
            logger.warning(
                "Windows Event Log requested but pywin32 is not installed. "
                "Install pywin32 to enable event logging."
            )

    return logger


def get_shutdown_event(service_name: str) -> threading.Event:
    """Get or create a shutdown event for a service."""
    if service_name not in _shutdown_events:
        _shutdown_events[service_name] = threading.Event()
    return _shutdown_events[service_name]


def trigger_shutdown(service_name: str) -> None:
    """Trigger shutdown for a service."""
    event = get_shutdown_event(service_name)
    event.set()


def setup_signal_handlers(service_name: str, logger: logging.Logger) -> None:
    """Set up signal handlers for graceful shutdown."""

    def signal_handler(signum: int, frame: Optional[FrameType]) -> None:
        logger.info(f"Received signal {signum}, initiating shutdown")
        trigger_shutdown(service_name)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
