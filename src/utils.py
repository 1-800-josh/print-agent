"""Utility functions for Print Agent."""

import logging
import os
import signal
import threading
from types import FrameType
from typing import Dict, List, Optional

_shutdown_events: Dict[str, threading.Event] = {}


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


def generate_task_folder_name(order_id: str, task_id: str) -> str:
    """Generate folder name for a task with multiple images."""
    return f"{task_id}-{order_id}"


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


def setup_logging(name: str, log_dir: str, level: int = logging.INFO) -> logging.Logger:
    """Set up logging with file and console handlers."""
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers = []

    # File handler
    file_handler = logging.FileHandler(os.path.join(log_dir, f"{name}.log"), encoding="utf-8")
    file_handler.setLevel(level)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

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
