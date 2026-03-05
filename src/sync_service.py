"""Main Print Agent sync service."""

import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from src.api_client import APIClient
from src.config import AgentConfig
from src.file_watcher import FileWatcher
from src.order_sync import OrderSync
from src.utils import get_shutdown_event, setup_logging, setup_signal_handlers


class SyncService:
    """Main sync service combining order sync and file watching."""

    def __init__(self, config: AgentConfig, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or setup_logging("sync_service", self.config.LOG_DIR)

        # Shutdown handling
        self.shutdown_event = get_shutdown_event(self.config.SERVICE_NAME)

        # Track network paths for watching
        self._network_paths: List[str] = []
        self._last_config_refresh = 0

        # Create API client
        self.api_client = APIClient(
            base_url=self.config.API_BASE_URL,
            api_key=self.config.API_KEY,
            organisation_id=self.config.ORGANISATION_ID,
            uploadthing_app_id=self.config.UPLOADTHING_APP_ID,
            logger=self.logger,
        )

        # Create components (order_sync uses filesystem check for skip logic)
        self.order_sync = OrderSync(
            self.config,
            self.api_client,
            self.logger,
            file_exists_in_users=self._file_exists_in_users,
        )
        self.file_watcher: Optional[FileWatcher] = None
        self._task_id_lookup: Dict[
            Tuple[str, str], str
        ] = {}  # (order_id, artwork_group_id) -> task_id

    def get_task_id(self, order_id: str, artwork_group_id: str) -> Optional[str]:
        """Resolve task_id from order_id and artwork_group_id."""
        return self._task_id_lookup.get((order_id, artwork_group_id))

    def _build_watch_path(self, prefix: str, folder: str) -> str:
        """Build full watch path from prefix and folder."""
        if prefix:
            return os.path.join(prefix, folder)
        return folder

    def _file_exists_in_users(self, filename: str) -> bool:
        """Return True if filename exists under users folder (filesystem as source of truth)."""
        if not self.config.USERS_FOLDER or not self.config.NETWORK_DRIVE_PREFIX:
            return False
        prefix = (self.config.NETWORK_DRIVE_PREFIX or "").rstrip(os.sep)
        users_base = os.path.join(prefix, self.config.USERS_FOLDER)
        if not os.path.isdir(users_base):
            return False
        for p in Path(users_base).rglob(filename):
            if p.is_file():
                return True
        return False

    def _refresh_network_paths(self) -> None:
        """Refresh network paths from synced tasks.

        Network paths are now returned at the top level of the API response:
        - artworkNetworkPath: the folder path for downloading artworks
        - userNetworkPath: the folder path for user-related files
        """
        now = time.time()
        if now - self._last_config_refresh < self.config.CONFIG_REFRESH_INTERVAL_SECONDS:
            return

        try:
            # Fetch tasks and extract network paths from response
            response = self.api_client.fetch_tasks()
            tasks = response.tasks

            # Per-task material paths (used for reference; watch paths use artwork/user)
            unique_paths = set()
            task_id_lookup: Dict[Tuple[str, str], str] = {}
            for task in tasks:
                if task.network_path:
                    unique_paths.add(task.network_path)
                for artwork in task.artworks:
                    if task.order_id and artwork.artwork_group_id:
                        task_id_lookup[(task.order_id, artwork.artwork_group_id)] = task.task_id
            self._network_paths = list(unique_paths)
            self._task_id_lookup = task_id_lookup
            self._last_config_refresh = now

            self.logger.info(
                f"Refreshed network paths: artwork={self.config.ARTWORK_FOLDER}, "
                f"user={self.config.USERS_FOLDER}, material paths={len(self._network_paths)}"
            )
        except Exception as e:
            self.logger.error(f"Failed to refresh network paths: {e}")

    def _initialize_folder_structure(self) -> None:
        """Initialize folder structure for users.

        Creates:
        - {NETWORK_DRIVE_PREFIX}/{USERS_FOLDER}/
        - {NETWORK_DRIVE_PREFIX}/{USERS_FOLDER}/{user_id}-{first_name} {last_name}/ for each user
        """
        if not self.config.NETWORK_DRIVE_PREFIX:
            self.logger.warning(
                "NETWORK_DRIVE_PREFIX not configured, skipping folder initialization"
            )
            return

        try:
            user_network_path = self.config.USERS_FOLDER

            if not user_network_path:
                self.logger.warning("USERS_FOLDER not configured, skipping folder initialization")
                return

            base_path = os.path.join(self.config.NETWORK_DRIVE_PREFIX, user_network_path)
            os.makedirs(base_path, exist_ok=True)
            self.logger.info(f"Created base folder: {base_path}")

            users = self.api_client.fetch_users()
            for user in users:
                folder_name = f"{user.user_id}-{user.first_name} {user.last_name}"
                user_folder = os.path.join(base_path, folder_name)
                os.makedirs(user_folder, exist_ok=True)
                self.logger.info(f"Created user folder: {user_folder}")

            self.logger.info(f"Initialized folder structure for {len(users)} users")

        except Exception as e:
            self.logger.error(f"Failed to initialize folder structure: {e}")

    def _initialize_watcher(self) -> None:
        """Initialize file watcher if not already running.

        Watch paths must match where files are stored:
        - {prefix}/{artworkNetworkPath}/ for downloads (e.g. .../artworks/cast_vinyl_20260303/)
        - {prefix}/{userNetworkPath}/ for user folders
        Do NOT watch {prefix}/{material} - that is incorrect.
        """
        if self.file_watcher and self.file_watcher.is_running():
            return

        prefix = (self.config.NETWORK_DRIVE_PREFIX or "").rstrip(os.sep)
        watch_paths = []

        if self.config.ARTWORK_FOLDER:
            full_path = self._build_watch_path(prefix, self.config.ARTWORK_FOLDER)
            watch_paths.append(full_path)
        if self.config.USERS_FOLDER:
            full_path = self._build_watch_path(prefix, self.config.USERS_FOLDER)
            watch_paths.append(full_path)

        if not watch_paths:
            self.logger.warning("No network paths configured, skipping file watcher")
            return

        user_paths = []
        if self.config.USERS_FOLDER:
            user_paths.append(self._build_watch_path(prefix, self.config.USERS_FOLDER))

        self.file_watcher = FileWatcher(
            self.api_client,
            watch_paths,
            user_paths=user_paths,
            movement_log_dir=self.config.MOVEMENT_LOG_DIR,
            logger=self.logger,
            get_task_id=self.get_task_id,
            debounce_seconds=self.config.FILE_EVENT_DEBOUNCE_SECONDS,
        )
        self.file_watcher.start()

    def run_sync_cycle(self) -> None:
        """Run a single sync cycle."""
        self.logger.info("Starting sync cycle")

        try:
            result = self.order_sync.sync_orders()

            if result.success:
                self.logger.info(
                    f"Sync cycle completed: {result.downloaded} downloaded, {result.failed} failed"
                )
            else:
                self.logger.error(
                    f"Sync cycle had errors: {result.downloaded} downloaded, {result.failed} failed"
                )
                for error in result.errors[:5]:  # Log first 5 errors
                    self.logger.error(f"  - {error}")

            # Refresh network paths after sync
            self._refresh_network_paths()

        except Exception as e:
            self.logger.exception(f"Error in sync cycle: {e}")

    def run(self) -> None:
        """Main service loop."""
        self.logger.info("=" * 50)
        self.logger.info("Print Agent Sync Service Starting")
        self.logger.info("=" * 50)
        self.logger.info(f"Organisation: {self.config.ORGANISATION_ID}")
        self.logger.info(f"API URL: {self.config.API_BASE_URL}")
        self.logger.info(f"Sync interval: {self.config.SYNC_INTERVAL_SECONDS}s")
        self.logger.info(f"Max workers: {self.config.MAX_WORKERS}")

        # Set up signal handlers
        setup_signal_handlers(self.config.SERVICE_NAME, self.logger)

        # Initialize folder structure
        self._initialize_folder_structure()

        # Initial sync and config refresh
        self._refresh_network_paths()
        self.run_sync_cycle()

        # Start file watcher
        self._initialize_watcher()

        # Main loop
        last_sync = time.time()

        while not self.shutdown_event.is_set():
            try:
                # Process file watcher events
                if self.file_watcher:
                    self.file_watcher.process_events()

                # Check if it's time for a sync
                now = time.time()
                if now - last_sync >= self.config.SYNC_INTERVAL_SECONDS:
                    self.run_sync_cycle()
                    last_sync = now

                # Periodic config refresh
                self._refresh_network_paths()
                if self.file_watcher and not self.file_watcher.is_running():
                    self._initialize_watcher()

                # Small sleep to prevent busy waiting
                time.sleep(0.1)

            except Exception as e:
                self.logger.exception(f"Error in main loop: {e}")
                time.sleep(5)  # Wait a bit before retrying

        # Cleanup
        self.logger.info("Shutting down...")
        if self.file_watcher:
            self.file_watcher.stop()
        self.logger.info("Print Agent Sync Service stopped")

    def run_once(self) -> None:
        """Run a single sync without entering the main loop."""
        self.logger.info("Running single sync cycle")
        self._initialize_folder_structure()
        self._refresh_network_paths()
        self.run_sync_cycle()
