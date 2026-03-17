#!/usr/bin/env python3
"""
Print Agent - Entry Point

Usage:
    python main.py service           # Run the sync service
    python main.py sync              # Run one sync cycle
    python main.py windows-install   # Install Windows service
    python main.py windows-start     # Start Windows service
    python main.py windows-stop      # Stop Windows service
    python main.py windows-remove    # Remove Windows service
    python main.py windows-service   # Run service mode (foreground)
    python main.py --help            # Show help
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.config import AgentConfig
from src.health_reporting import configure_status_reporting, emit_status_event
from src.sync_service import SyncService
from src.utils import setup_logging, setup_signal_handlers


def run_service(args: argparse.Namespace) -> None:
    """Run the sync service."""
    config = load_config(args)
    logger = setup_logging(
        "sync_service",
        config.LOG_DIR,
        instance_id=config.INSTANCE_ID,
        force_event_log=(sys.platform == "win32"),
        event_source=config.SERVICE_NAME
    )
    configure_status_reporting(
        enabled=config.STRUCTURED_STATUS_STDOUT_ENABLED,
        service_name=config.SERVICE_NAME,
        instance_id=config.INSTANCE_ID,
        heartbeat_interval_seconds=config.HEALTH_HEARTBEAT_INTERVAL_SECONDS,
    )
    emit_status_event("service_starting", state="starting", healthy=True)
    setup_signal_handlers(config.SERVICE_NAME, logger)
    logger.info("Starting Print Agent Sync Service (CLI)")

    service = SyncService(config, logger)
    service.run()


def run_sync(args: argparse.Namespace) -> None:
    """Run a single sync cycle."""
    config = load_config(args)
    logger = setup_logging(
        "sync_service",
        config.LOG_DIR,
        instance_id=config.INSTANCE_ID,
        event_source=config.SERVICE_NAME
    )
    configure_status_reporting(
        enabled=config.STRUCTURED_STATUS_STDOUT_ENABLED,
        service_name=config.SERVICE_NAME,
        instance_id=config.INSTANCE_ID,
        heartbeat_interval_seconds=config.HEALTH_HEARTBEAT_INTERVAL_SECONDS,
    )
    emit_status_event("sync_mode_starting", state="starting", healthy=True)

    service = SyncService(config, logger)
    service.run_once()


def load_config(args: argparse.Namespace) -> AgentConfig:
    """Load configuration from file."""
    config_path = args.config or os.getenv("PRINT_AGENT_CONFIG") or "config.json"
    try:
        return AgentConfig.from_file(config_path)
    except FileNotFoundError:
        emit_status_event(
            "config_error",
            level="error",
            state="config_error",
            healthy=False,
            details={"config_path": config_path, "error": "Config file not found"},
            force=True,
        )
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        emit_status_event(
            "config_error",
            level="error",
            state="config_error",
            healthy=False,
            details={"config_path": config_path, "error": str(e)},
            force=True,
        )
        print(f"Error: Invalid JSON in config file {config_path}: {e}", file=sys.stderr)
        sys.exit(1)


def get_config_path(args: argparse.Namespace) -> str:
    """Resolve configuration path from args/env/default."""
    return args.config or os.getenv("PRINT_AGENT_CONFIG") or "config.json"


def ensure_windows_command(command: str) -> None:
    """Exit with error if a Windows-only command runs on non-Windows."""
    if sys.platform != "win32":
        print(
            f"Error: '{command}' is only available on Windows.",
            file=sys.stderr,
        )
        sys.exit(1)


def run_windows_service(args: argparse.Namespace) -> None:
    """Run service mode in foreground with Windows Event Log enabled."""
    ensure_windows_command("windows-service")
    config = load_config(args)
    from windows_service import run_service_host
    run_service_host(config)


def run_windows_install(args: argparse.Namespace) -> None:
    """Install the Windows service."""
    ensure_windows_command("windows-install")
    config = load_config(args)
    config_path = get_config_path(args)
    from windows_service import install_service
    service_name = install_service(config, config_path)
    print(f"Installed Windows service: {service_name}")


def run_windows_start(args: argparse.Namespace) -> None:
    """Start the Windows service."""
    ensure_windows_command("windows-start")
    config = load_config(args)
    from windows_service import start_service
    start_service(config.SERVICE_NAME)
    print(f"Started Windows service: {config.SERVICE_NAME}")


def run_windows_stop(args: argparse.Namespace) -> None:
    """Stop the Windows service."""
    ensure_windows_command("windows-stop")
    config = load_config(args)
    from windows_service import stop_service
    stop_service(config.SERVICE_NAME)
    print(f"Stopped Windows service: {config.SERVICE_NAME}")


def run_windows_remove(args: argparse.Namespace) -> None:
    """Remove the Windows service."""
    ensure_windows_command("windows-remove")
    config = load_config(args)
    from windows_service import remove_service
    remove_service(config.SERVICE_NAME)
    print(f"Removed Windows service: {config.SERVICE_NAME}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print Agent - Background service for print task processing"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "service",
            "sync",
            "windows-service",
            "windows-install",
            "windows-start",
            "windows-stop",
            "windows-remove",
        ],
        help="Command to run",
    )
    parser.add_argument(
        "--config", help="Path to config JSON file (default: config.json)"
    )
    parser.add_argument("--version", action="version", version="Print Agent v0.1.0")

    args = parser.parse_args()

    try:
        if args.command == "service":
            run_service(args)
        elif args.command == "sync":
            run_sync(args)
        elif args.command == "windows-service":
            run_windows_service(args)
        elif args.command == "windows-install":
            run_windows_install(args)
        elif args.command == "windows-start":
            run_windows_start(args)
        elif args.command == "windows-stop":
            run_windows_stop(args)
        elif args.command == "windows-remove":
            run_windows_remove(args)
        else:
            parser.print_help()
            sys.exit(1)
    except RuntimeError as e:
        emit_status_event(
            "fatal_error",
            level="error",
            state="fatal_error",
            healthy=False,
            details={"error": str(e)},
            force=True,
        )
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        emit_status_event(
            "fatal_error",
            level="error",
            state="fatal_error",
            healthy=False,
            details={"error": str(e)},
            force=True,
        )
        raise


if __name__ == "__main__":
    main()
