"""Main Print Agent sync service."""

import logging
import os
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from src.api_client import APIClient, PrintingTask
from src.config import AgentConfig
from src.file_watcher import FileWatcher
from src.order_sync import OrderSync
from src.utils import get_shutdown_event, setup_logging, setup_signal_handlers


class SyncService:
    """Main sync service combining order sync and file watching."""

    def __init__(self, config: AgentConfig, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or setup_logging(
            "agent",
            self.config.LOG_DIR,
            posthog_enabled=self.config.POSTHOG_ENABLED,
            posthog_config={
                "api_key": self.config.POSTHOG_PROJECT_API_KEY,
                "host": self.config.POSTHOG_HOST,
                "organisation_id": self.config.ORGANISATION_ID,
            } if self.config.POSTHOG_ENABLED else None,
        )

        # Shutdown handling
        self.shutdown_event = get_shutdown_event(self.config.SERVICE_NAME)

        # Track network paths for watching
        self._network_paths: List[str] = []
        self._last_config_refresh = 0
        self._tasks: List[PrintingTask] = []
        self._last_sync_started_at: Optional[str] = None
        self._last_successful_sync_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._consecutive_sync_failures = 0
        self._consecutive_operational_failures = 0
        self._last_heartbeat_at = 0.0

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
            should_skip_artwork_download=self._should_skip_artwork_download,
        )
        self.file_watcher: Optional[FileWatcher] = None

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

    def _should_skip_artwork_download(self, filename: str) -> bool:
        """Return True if filename was recently deleted from artwork (move in progress)."""
        if not self.file_watcher or not self.file_watcher.is_running():
            return False
        return self.file_watcher.is_pending_artwork_move(filename)

    def _extract_task_id_from_filename(self, filename: str) -> Optional[str]:
        """Extract task_id from filename. Format: {task_id}-{order_id}[-...].ext or {task_id}-{order_id}.zip"""
        name, _ = os.path.splitext(filename)
        if not name:
            return None
        if "_image_" in name:
            base_part = name.split("_image_")[0]
        else:
            base_part = name
        parts = base_part.split("-")
        return parts[0] if len(parts) >= 2 else None

    def _is_artwork_or_zip_file(self, filename: str) -> bool:
        """Return True if file is an artwork or zip we track."""
        ext = os.path.splitext(filename)[1].lower()
        return ext in (".png", ".jpg", ".jpeg", ".pdf", ".zip")

    def _is_material_date_subfolder(self, folder_name: str) -> bool:
        """Return True if folder matches {material}_{YYYYMMDD} pattern."""
        return bool(re.match(r".+_\d{8}$", folder_name))

    def _extract_user_id_from_folder(self, folder_name: str) -> Optional[str]:
        """Extract user_id from folder name. Format: {user_id}-{user_name}"""
        if "-" in folder_name:
            return folder_name[: folder_name.index("-")]
        return folder_name if folder_name else None

    def _now_iso(self) -> str:
        """Return the current UTC timestamp in ISO-8601 format."""
        return datetime.now(timezone.utc).isoformat()

    def _current_status_details(self) -> Dict[str, object]:
        """Build current service health details for structured status events."""
        watcher_running = self.file_watcher.is_running() if self.file_watcher else False
        return {
            "watcher_running": watcher_running,
            "last_sync_started_at": self._last_sync_started_at,
            "last_successful_sync_at": self._last_successful_sync_at,
            "consecutive_sync_failures": self._consecutive_sync_failures,
            "consecutive_operational_failures": self._consecutive_operational_failures,
            "last_error": self._last_error,
            "task_count": len(self._tasks),
        }

    def _current_health_state(self) -> Tuple[str, bool]:
        """Return the current derived health state."""
        healthy = self._last_error is None and self._consecutive_operational_failures == 0
        return ("healthy" if healthy else "degraded", healthy)

    def _build_fs_state(
        self,
    ) -> Dict[str, Tuple[Optional[str], List[str]]]:
        """Build filesystem state: task_id -> (assigned_user_id or None, list of file paths)."""
        fs_state: Dict[str, Tuple[Optional[str], List[str]]] = {}
        prefix = (self.config.NETWORK_DRIVE_PREFIX or "").rstrip(os.sep)
        if not prefix:
            return fs_state

        # Scan material_date subfolders under artworks
        if self.config.ARTWORK_FOLDER:
            artworks_base = os.path.join(prefix, self.config.ARTWORK_FOLDER)
            if os.path.isdir(artworks_base):
                for subdir in Path(artworks_base).iterdir():
                    if subdir.is_dir() and self._is_material_date_subfolder(subdir.name):
                        for f in subdir.iterdir():
                            if f.is_file() and self._is_artwork_or_zip_file(f.name):
                                task_id = self._extract_task_id_from_filename(f.name)
                                if task_id:
                                    path_str = str(f)
                                    if task_id not in fs_state:
                                        fs_state[task_id] = (None, [])
                                    fs_state[task_id][1].append(path_str)

        # Scan user subfolders under users (overwrites artworks entry if same task in user folder)
        if self.config.USERS_FOLDER:
            users_base = os.path.join(prefix, self.config.USERS_FOLDER)
            if os.path.isdir(users_base):
                for user_subdir in Path(users_base).iterdir():
                    if user_subdir.is_dir():
                        user_id = self._extract_user_id_from_folder(user_subdir.name)
                        if user_id:
                            for f in user_subdir.iterdir():
                                if f.is_file() and self._is_artwork_or_zip_file(f.name):
                                    task_id = self._extract_task_id_from_filename(f.name)
                                    if task_id:
                                        path_str = str(f)
                                        if task_id not in fs_state or fs_state[task_id][0] != user_id:
                                            fs_state[task_id] = (user_id, [])
                                        fs_state[task_id][1].append(path_str)

        return fs_state

    def _reconcile_task_states(self) -> None:
        """Reconcile file locations with task state in DB. Filesystem is source of truth."""
        if not self.config.RECONCILE_TASK_STATES or not self.config.NETWORK_DRIVE_PREFIX:
            return
        if not self._tasks:
            return

        fs_state = self._build_fs_state()
        task_by_id = {t.task_id: t for t in self._tasks}
        assigned_count = 0
        unassigned_count = 0
        removed_count = 0

        for task in self._tasks:
            task_id = task.task_id
            db_assigned = task.assigned_user_id
            db_completed = task.status.upper() == "COMPLETED"
            fs_entry = fs_state.get(task_id)

            if db_completed and fs_entry:
                # File exists but task completed -> remove file
                _, paths = fs_entry
                for path in paths:
                    try:
                        os.remove(path)
                        removed_count += 1
                        self.logger.info(f"Removed stale file (task completed): {path}", extra={'category': 'reconcile'})
                    except OSError as e:
                        self.logger.warning(f"Failed to remove stale file {path}: {e}", extra={'category': 'reconcile'})
                continue

            if fs_entry:
                fs_user_id, paths = fs_entry
                if fs_user_id and fs_user_id != db_assigned:
                    if self.api_client.assign_task(task_ids=[task_id], user_id=fs_user_id):
                        assigned_count += 1
                        self.logger.info(f"Reconciled: assigned task {task_id} to user {fs_user_id}", extra={'category': 'reconcile'})
                elif not fs_user_id and db_assigned:
                    if self.api_client.unassign_task(task_id):
                        unassigned_count += 1
                        self.logger.info(f"Reconciled: unassigned task {task_id}", extra={'category': 'reconcile'})
            else:
                # File not found
                if db_assigned:
                    if self.api_client.unassign_task(task_id):
                        unassigned_count += 1
                        self.logger.info(
                            f"Task {task_id} assigned to user {db_assigned} but file not found; unassigned",
                            extra={'category': 'reconcile'}
                        )

        if assigned_count or unassigned_count or removed_count:
            self.logger.info(
                f"Reconciled: {assigned_count} assigned, {unassigned_count} unassigned, {removed_count} files removed",
                extra={'category': 'reconcile'}
            )

    def _cleanup_empty_artwork_folders(self) -> None:
        """Remove empty material_date subfolders under artworks."""
        if not self.config.CLEANUP_EMPTY_ARTWORK_FOLDERS or not self.config.NETWORK_DRIVE_PREFIX:
            self.logger.debug("Cleanup skipped: CLEANUP_EMPTY_ARTWORK_FOLDERS=%s or no NETWORK_DRIVE_PREFIX", self.config.CLEANUP_EMPTY_ARTWORK_FOLDERS)
            return
        if not self.config.ARTWORK_FOLDER:
            return

        prefix = (self.config.NETWORK_DRIVE_PREFIX or "").rstrip(os.sep)
        artworks_base = os.path.join(prefix, self.config.ARTWORK_FOLDER)
        if not os.path.isdir(artworks_base):
            self.logger.debug("Cleanup skipped: artworks_base not a dir: %s", artworks_base)
            return

        removed = 0
        IGNORED_ENTRIES = {".DS_Store", "Thumbs.db", ".Spotlight-V100", ".Trashes"}
        for subdir in list(Path(artworks_base).iterdir()):
            if not subdir.is_dir() or not self._is_material_date_subfolder(subdir.name):
                continue
            try:
                entries = list(subdir.iterdir())
                significant = [e for e in entries if e.name not in IGNORED_ENTRIES and not e.name.startswith(".")]
                if not significant:
                    for e in entries:
                        try:
                            e.unlink()
                        except OSError:
                            pass
                    subdir.rmdir()
                    removed += 1
                    self.logger.info(f"Removed empty artwork folder: {subdir}", extra={'category': 'cleanup'})
            except OSError as e:
                self.logger.warning("Could not remove folder %s: %s", subdir, e, extra={'category': 'cleanup'})

        if removed:
            self.logger.info(f"Cleaned up {removed} empty artwork folder(s)", extra={'category': 'cleanup'})

    def _refresh_network_paths(self, force: bool = False) -> None:
        """Refresh network paths from synced tasks.

        Network paths are now returned at the top level of the API response:
        - artworkNetworkPath: the folder path for downloading artworks
        - userNetworkPath: the folder path for user-related files
        """
        now = time.time()
        if not force and now - self._last_config_refresh < self.config.CONFIG_REFRESH_INTERVAL_SECONDS:
            return

        try:
            # Fetch tasks and extract network paths from response
            response = self.api_client.fetch_tasks()
            tasks = response.tasks
            self._tasks = tasks

            # Per-task material paths (used for reference; watch paths use artwork/user)
            unique_paths = set()
            for task in tasks:
                if task.network_path:
                    unique_paths.add(task.network_path)
            self._network_paths = list(unique_paths)
            self._last_config_refresh = now

            self.logger.info(
                f"Refreshed network paths: artwork={self.config.ARTWORK_FOLDER}, "
                f"user={self.config.USERS_FOLDER}, material paths={len(self._network_paths)}",
                extra={'category': 'sync'}
            )
        except Exception as e:
            self._last_error = str(e)
            self._consecutive_operational_failures += 1
            self.logger.error(
                f"Failed to refresh network paths: {e}",
                extra={
                    'category': 'sync',
                    'state': 'degraded',
                    'healthy': False,
                    'details': {"error": str(e), **self._current_status_details()},
                }
            )

    def _initialize_folder_structure(self) -> None:
        """Initialize folder structure for users.

        Creates:
        - {NETWORK_DRIVE_PREFIX}/{USERS_FOLDER}/
        - {NETWORK_DRIVE_PREFIX}/{USERS_FOLDER}/{user_id}-{first_name} {last_name}/ for each user
        """
        if not self.config.NETWORK_DRIVE_PREFIX:
            self.logger.warning(
                "NETWORK_DRIVE_PREFIX not configured, skipping folder initialization",
                extra={'category': 'service'}
            )
            return

        try:
            user_network_path = self.config.USERS_FOLDER

            if not user_network_path:
                self.logger.warning("USERS_FOLDER not configured, skipping folder initialization", extra={'category': 'service'})
                return

            base_path = os.path.join(self.config.NETWORK_DRIVE_PREFIX, user_network_path)
            os.makedirs(base_path, exist_ok=True)
            self.logger.info(f"Created base folder: {base_path}", extra={'category': 'service'})

            users = self.api_client.fetch_users()
            for user in users:
                folder_name = f"{user.user_id}-{user.first_name} {user.last_name}"
                user_folder = os.path.join(base_path, folder_name)
                os.makedirs(user_folder, exist_ok=True)
                self.logger.info(f"Created user folder: {user_folder}", extra={'category': 'service'})

            self.logger.info(f"Initialized folder structure for {len(users)} users", extra={'category': 'service'})

        except Exception as e:
            self._last_error = str(e)
            self._consecutive_operational_failures += 1
            self.logger.error(
                f"Failed to initialize folder structure: {e}",
                extra={
                    'category': 'service',
                    'state': 'degraded',
                    'healthy': False,
                    'details': {"error": str(e), **self._current_status_details()},
                }
            )

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
            self.logger.warning("No network paths configured, skipping file watcher", extra={'category': 'watcher'})
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
            debounce_seconds=self.config.FILE_EVENT_DEBOUNCE_SECONDS,
            posthog_config={
                "posthog_api_key": self.config.POSTHOG_PROJECT_API_KEY,
                "posthog_host": self.config.POSTHOG_HOST,
                "organisation_id": self.config.ORGANISATION_ID,
                "instance_id": self.config.INSTANCE_ID,
                "machine_name": socket.gethostname(),
            } if self.config.POSTHOG_ENABLED else None,
        )
        self.file_watcher.start()
        self.logger.info(
            "Watcher started",
            extra={
                'category': 'watcher',
                'state': 'healthy',
                'healthy': True,
                'details': {"watch_paths": watch_paths},
            }
        )

    def run_sync_cycle(self) -> None:
        """Run a single sync cycle."""
        self._last_sync_started_at = self._now_iso()
        current_state, healthy = self._current_health_state()
        self.logger.info(
            "Sync cycle started",
            extra={
                'category': 'sync',
                'state': current_state,
                'healthy': healthy,
                'details': self._current_status_details(),
            }
        )

        try:
            # 1. Refresh paths / fetch tasks (need fresh data for reconciliation)
            self._refresh_network_paths(force=True)

            # 2. Reconcile task states (fs vs DB)
            self._reconcile_task_states()

            # 3. Clean empty artwork folders
            self._cleanup_empty_artwork_folders()

            # 4. Order sync (download new files)
            result = self.order_sync.sync_orders()

            if result.success:
                self._last_successful_sync_at = self._now_iso()
                self._last_error = None
                self._consecutive_sync_failures = 0
                self._consecutive_operational_failures = 0
                self.logger.info(
                    f"Sync cycle completed: {result.downloaded} downloaded, {result.failed} failed",
                    extra={
                        'category': 'sync',
                        'state': 'healthy',
                        'healthy': True,
                        'details': {
                            "downloaded": result.downloaded,
                            "failed": result.failed,
                            **self._current_status_details(),
                        },
                    }
                )
            else:
                self._consecutive_sync_failures += 1
                self._consecutive_operational_failures += 1
                self._last_error = result.errors[0] if result.errors else "Sync cycle had errors"
                self.logger.error(
                    f"Sync cycle had errors: {result.downloaded} downloaded, {result.failed} failed",
                    extra={
                        'category': 'sync',
                        'state': 'degraded',
                        'healthy': False,
                        'details': {
                            "downloaded": result.downloaded,
                            "failed": result.failed,
                            "errors": result.errors[:5],
                            **self._current_status_details(),
                        },
                    }
                )
                for error in result.errors[:5]:  # Log first 5 errors
                    self.logger.error(f"  - {error}", extra={'category': 'sync'})

        except Exception as e:
            self._consecutive_sync_failures += 1
            self._consecutive_operational_failures += 1
            self._last_error = str(e)
            self.logger.error(
                f"Error in sync cycle: {e}",
                extra={
                    'category': 'sync',
                    'state': 'degraded',
                    'healthy': False,
                    'details': {"error": str(e), **self._current_status_details()},
                },
                exc_info=True
            )

    def run(self) -> None:
        """Main service loop."""
        self.logger.info(
            "Service started",
            extra={
                'category': 'service',
                'state': 'starting',
                'healthy': True,
                'details': self._current_status_details(),
            }
        )
        self.logger.info("=" * 50, extra={'category': 'service'})
        self.logger.info("Print Agent Sync Service Starting", extra={'category': 'service'})
        self.logger.info("=" * 50, extra={'category': 'service'})
        self.logger.info(f"Organisation: {self.config.ORGANISATION_ID}", extra={'category': 'service'})
        self.logger.info(f"API URL: {self.config.API_BASE_URL}", extra={'category': 'service'})
        self.logger.info(f"Sync interval: {self.config.SYNC_INTERVAL_SECONDS}s", extra={'category': 'service'})
        self.logger.info(f"Max workers: {self.config.MAX_WORKERS}", extra={'category': 'service'})

        # Set up signal handlers
        setup_signal_handlers(self.config.SERVICE_NAME, self.logger)

        # Initialize folder structure
        self._initialize_folder_structure()

        # Initial sync and config refresh
        self._refresh_network_paths()
        self.run_sync_cycle()

        # Start file watcher
        self._initialize_watcher()
        current_state, healthy = self._current_health_state()
        self._last_heartbeat_at = time.time()
        self.logger.info(
            "Heartbeat",
            extra={
                'category': 'service',
                'state': current_state,
                'healthy': healthy,
                'details': self._current_status_details(),
            }
        )

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

                # Emit heartbeat if interval elapsed
                now = time.time()
                if now - self._last_heartbeat_at >= self.config.HEALTH_HEARTBEAT_INTERVAL_SECONDS:
                    self._last_heartbeat_at = now
                    current_state, healthy = self._current_health_state()
                    self.logger.info(
                        "Heartbeat",
                        extra={
                            'category': 'service',
                            'state': current_state,
                            'healthy': healthy,
                            'details': self._current_status_details(),
                        }
                    )

                # Small sleep to prevent busy waiting
                time.sleep(0.1)

            except Exception as e:
                self._last_error = str(e)
                self.logger.error(
                    f"Error in main loop: {e}",
                    extra={
                        'category': 'service',
                        'state': 'degraded',
                        'healthy': False,
                        'details': {"error": str(e), **self._current_status_details()},
                    },
                    exc_info=True
                )
                time.sleep(5)  # Wait a bit before retrying

        # Cleanup
        self.logger.info(
            "Shutdown started",
            extra={
                'category': 'service',
                'state': 'stopping',
                'healthy': True,
                'details': self._current_status_details(),
            }
        )
        self.logger.info("Shutting down...", extra={'category': 'service'})
        if self.file_watcher:
            self.file_watcher.stop()
            self.logger.info(
                "Watcher stopped",
                extra={
                    'category': 'watcher',
                    'state': 'stopping',
                    'healthy': True,
                    'details': {"watcher_running": False},
                }
            )
        self.logger.info("Print Agent Sync Service stopped", extra={'category': 'service'})
        self.logger.info(
            "Shutdown complete",
            extra={
                'category': 'service',
                'state': 'stopped',
                'healthy': True,
                'details': self._current_status_details(),
            }
        )

    def run_once(self) -> None:
        """Run a single sync without entering the main loop."""
        self.logger.info("Service started", extra={'category': 'service', 'state': 'starting', 'healthy': True})
        self.logger.info("Running single sync cycle", extra={'category': 'service'})
        self._initialize_folder_structure()
        self._refresh_network_paths()
        self.run_sync_cycle()
        self.logger.info(
            "Shutdown complete",
            extra={
                'category': 'service',
                'state': 'stopped',
                'healthy': self._consecutive_sync_failures == 0,
                'details': self._current_status_details(),
            }
        )
