#!/usr/bin/env python3
"""
Print Agent - Entry Point

Usage:
    python main.py service           # Run the sync service
    python main.py sync              # Run one sync cycle
    python main.py --help            # Show help
"""

import argparse
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv

from src.config import AgentConfig
from src.sync_service import SyncService
from src.utils import setup_logging, setup_signal_handlers

load_dotenv()


def run_service() -> None:
    """Run the sync service."""
    config = AgentConfig.from_env()
    logger = setup_logging("sync_service", config.LOG_DIR)
    setup_signal_handlers(config.SERVICE_NAME, logger)
    logger.info("Starting Print Agent Sync Service (CLI)")

    service = SyncService(config, logger)
    service.run()


def run_sync() -> None:
    """Run a single sync cycle."""
    config = AgentConfig.from_env()
    logger = setup_logging("sync_service", config.LOG_DIR)

    service = SyncService(config, logger)
    service.run_once()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print Agent - Background service for print task processing"
    )
    parser.add_argument(
        "command", nargs="?", choices=["service", "sync"], help="Command to run"
    )
    parser.add_argument("--version", action="version", version="Print Agent v0.1.0")

    args = parser.parse_args()

    if args.command == "service":
        run_service()
    elif args.command == "sync":
        run_sync()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
