"""File system watcher for Print Agent."""

import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from src.api_client import APIClient


class DebouncedEventHandler:
    """Handler that debounces rapid file events."""

    def __init__(self, callback: Callable[[str, str], None], debounce_seconds: float = 2.0):
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._pending_events: Dict[str, float] = {}
        self._last_call: Dict[str, float] = {}

    def trigger(self, event_type: str, file_path: str) -> None:
        """Trigger an event with debouncing."""
        key = f"{event_type}:{file_path}"
        now = time.time()

        # Check if we recently processed this event
        if key in self._last_call:
            if now - self._last_call[key] < self.debounce_seconds:
                return

        self._pending_events[key] = now

    def process_pending(self) -> None:
        """Process pending events that have passed debounce window."""
        now = time.time()
        to_process = []

        for key, timestamp in list(self._pending_events.items()):
            if now - timestamp >= self.debounce_seconds:
                to_process.append(key)
                del self._pending_events[key]

        for key in to_process:
            event_type, file_path = key.split(":", 1)
            self._last_call[key] = now
            try:
                self.callback(event_type, file_path)
            except Exception as e:
                logging.getLogger(__name__).error(f"Error processing event {key}: {e}")


class HotFolderEventHandler(FileSystemEventHandler):
    """Handler for hot folder file system events."""

    def __init__(
        self,
        api_client: APIClient,
        network_paths: List[str],
        user_paths: Optional[List[str]] = None,
        completed_paths: Optional[List[str]] = None,
        logger: Optional[logging.Logger] = None,
        movement_logger: Optional[logging.Logger] = None,
        debounce_seconds: float = 2.0,
        recent_event_threshold: int = 15,
        cleanup_interval: int = 30,
    ):
        self.api_client = api_client
        self.network_paths = set(network_paths)
        self.user_paths = set(user_paths or [])
        self.completed_paths = set(completed_paths or [])
        self.logger = logger or logging.getLogger(__name__)
        self.movement_logger = movement_logger
        self.debounce_seconds = debounce_seconds
        self._recent_event_threshold = recent_event_threshold
        self._cleanup_interval = cleanup_interval

        # Debounced handlers
        self._debouncer = DebouncedEventHandler(self._handle_event, debounce_seconds)

        # Track file states
        self._file_users: Dict[str, Optional[str]] = {}  # file_path -> user_id or None
        self._folder_users: Dict[str, Optional[str]] = {}  # folder_path -> user_id or None
        self._moved_in_sources: Dict[str, str] = {}  # dest_path -> src_path (for logging)
        self._reassign_sources: Dict[str, str] = {}  # dest_path -> src_path (user-to-user move)
        self._recent_artwork_deletions: Dict[str, tuple] = {}  # filename -> (src_path, timestamp)
        self._recent_user_folder_creates: Dict[
            str, tuple
        ] = {}  # filename -> (dest_path, timestamp)
        self._moved_to_completed_sources: Dict[str, str] = {}  # dest_path -> src_path
        self._recent_user_deletes_for_completed: Dict[str, tuple] = {}  # filename -> (src_path, ts)
        self._recent_completed_task_ids: Dict[str, float] = {}  # task_id -> ts (dedupe completion)

    def is_pending_artwork_move(self, filename: str) -> bool:
        """Return True if filename was recently deleted from artwork (move in progress)."""
        entry = self._recent_artwork_deletions.get(filename)
        if not entry:
            return False
        _, ts = entry
        return time.time() - ts < self._recent_event_threshold

    def _extract_task_info(self, file_path: str) -> Optional[Dict[str, Optional[str]]]:
        """Extract order_id and task_id from filename.

        Filename format: {task_id}-{order_id}-{morse_code} or {task_id}-{order_id}-{morse_code}_image_{n}
        """
        filename = os.path.basename(file_path)
        name, _ = os.path.splitext(filename)
        if not name:
            return None

        if "_image_" in name:
            base_part = name.split("_image_")[0]
        else:
            base_part = name

        parts = base_part.split("-")

        if len(parts) >= 2:
            task_id = parts[0]
            order_id = parts[1]
        else:
            return None

        return {
            "order_id": order_id,
            "task_id": task_id,
            "filename": filename,
        }

    def _extract_user_info(self, file_path: str) -> Optional[Dict[str, Optional[str]]]:
        """Extract user_id and user_name from file path.

        Supported structure: {prefix}/{userNetworkPath}/{user_id}-{user_name}/{filename}
        e.g. /Users/.../bannershop-MES/users/123-John Doe/image.png → user_id="123", user_name="John Doe"

        Only paths under user_paths are treated as user folders (not artworks).
        """
        path_str = os.path.normpath(file_path)
        for base in self.user_paths:
            base_norm = os.path.normpath(base)
            if path_str.startswith(base_norm + os.sep):
                rel = os.path.relpath(path_str, base_norm)
                parts = rel.split(os.sep)
                if len(parts) >= 2 and not re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
                    folder_name = parts[0]
                    # Format: {user_id}-{user_name}
                    if "-" in folder_name:
                        first_dash = folder_name.index("-")
                        user_id = folder_name[:first_dash]
                        user_name = folder_name[first_dash + 1 :]
                        return {"user_id": user_id, "user_name": user_name}
                    return {"user_id": folder_name, "user_name": folder_name}
                break
        return None

    def _is_in_user_folder(self, file_path: str) -> bool:
        """Check if file is inside a user subfolder."""
        return self._extract_user_info(file_path) is not None

    def _is_in_completed_folder(self, file_path: str) -> bool:
        """True if path is under a configured completed-folder root."""
        path_str = os.path.normpath(file_path)
        for base in self.completed_paths:
            base_norm = os.path.normpath(base)
            if path_str == base_norm or path_str.startswith(base_norm + os.sep):
                return True
        return False

    @property
    def _artwork_search_roots(self) -> Set[str]:
        """Watch roots that are neither user folders nor completed (e.g. artworks)."""
        return self.network_paths - self.user_paths - self.completed_paths

    def _find_file_in_roots(self, filename: str, roots: Set[str]) -> bool:
        """Return True if filename exists as a file under any root (recursive)."""
        for root in roots:
            root_path = Path(root)
            if root_path.is_dir():
                for p in root_path.rglob(filename):
                    if p.is_file():
                        return True
        return False

    def _pending_moved_to_completed_for_basename(self, basename: str) -> bool:
        """True if a debounced moved_to_completed for this filename is pending."""
        for key in self._debouncer._pending_events:
            if not key.startswith("moved_to_completed:"):
                continue
            _, path = key.split(":", 1)
            if os.path.basename(path) == basename:
                return True
        return False

    def _is_recently_completed_task(self, task_id: str) -> bool:
        """True if we already completed this task within the recent-event window."""
        ts = self._recent_completed_task_ids.get(task_id)
        if not ts:
            return False
        if time.time() - ts > self._recent_event_threshold:
            del self._recent_completed_task_ids[task_id]
            return False
        return True

    def _record_recent_completed_task(self, task_id: str) -> None:
        self._recent_completed_task_ids[task_id] = time.time()

    def _complete_task_for_user_src(self, src_path: str, dest_path: str) -> None:
        """Call complete_task using user_id from src path; task_id from dest or src filename."""
        task_info = self._extract_task_info(dest_path) or self._extract_task_info(src_path)
        if not task_info:
            return

        task_id = task_info["task_id"]
        if not task_id:
            self.logger.warning(
                f"Cannot complete: no task_id in filename {task_info['filename']}",
                extra={'category': 'watcher'},
            )
            return

        if self._is_recently_completed_task(task_id):
            return

        user_info = self._extract_user_info(src_path)
        if not user_info:
            return

        user_id = user_info["user_id"]
        user_name = user_info["user_name"]

        self.logger.info(
            f"Task completed (moved to completed folder): {dest_path} (user: {user_name}, task: {task_id})",
            extra={'category': 'watcher'},
        )
        if self.movement_logger:
            self.movement_logger.info(
                f"Completed (moved to completed): {dest_path} src={src_path} (user: {user_id}, task: {task_id})",
                extra={
                    'category': 'movement',
                    'pathname': dest_path,
                    'details': {'user_id': user_id, 'task_id': task_id, 'src_path': src_path},
                },
            )

        if user_id and self.api_client.complete_task(task_id, user_id):
            if src_path in self._file_users:
                del self._file_users[src_path]
            self._record_recent_completed_task(task_id)
            self.logger.info(
                f"Completed task {task_id} by user {user_name}", extra={'category': 'watcher'}
            )

    def _handle_moved_to_completed(self, dest_path: str) -> None:
        """Handle file moved or copied into the completed folder."""
        src_path = self._moved_to_completed_sources.pop(dest_path, None)
        if not src_path:
            return
        self._complete_task_for_user_src(src_path, dest_path)

    def _handle_event(self, event_type: str, file_path: str) -> None:
        """Handle a debounced file event."""
        if event_type == "moved_in":
            self._handle_moved_in(file_path)
        elif event_type == "moved_out":
            self._handle_moved_out(file_path)
        elif event_type == "reassigned":
            self._handle_reassigned(file_path)
        elif event_type == "moved_to_completed":
            self._handle_moved_to_completed(file_path)
        elif event_type == "deleted":
            self._handle_deleted(file_path)

    def _handle_moved_in(self, file_path: str) -> None:
        """Handle file moved into user folder."""
        user_info = self._extract_user_info(file_path)
        if not user_info:
            return

        user_id = user_info["user_id"]
        user_name = user_info["user_name"]

        task_info = self._extract_task_info(file_path)
        if not task_info:
            return

        src_path = self._moved_in_sources.pop(file_path, None)
        if not src_path:
            filename = os.path.basename(file_path)
            entry = self._recent_artwork_deletions.pop(filename, None)
            if entry:
                cand_path, cand_ts = entry
                if time.time() - cand_ts < self._recent_event_threshold:
                    src_path = cand_path
            if not src_path:
                now = time.time()
                self._recent_artwork_deletions = {
                    k: v
                    for k, v in self._recent_artwork_deletions.items()
                    if now - v[1] < self._cleanup_interval
                }
        from_folder = os.path.dirname(src_path) if src_path else "(unknown)"

        self.logger.info(f"File moved to user folder: {file_path} (user: {user_name})", extra={'category': 'watcher'})
        if self.movement_logger:
            self.movement_logger.info(
                f"Moved in: {file_path} from {from_folder} -> user: {user_id}",
                extra={
                    'category': 'movement',
                    'pathname': file_path,
                    'details': {'from_folder': from_folder, 'user_id': user_id}
                }
            )
        task_id = task_info["task_id"]
        if not task_id:
            self.logger.warning(f"Cannot assign: no task_id in filename {task_info['filename']}", extra={'category': 'watcher'})
            return

        if self.api_client.assign_task(task_ids=[task_id], user_id=user_id):
            self._file_users[file_path] = user_id
            self.logger.info(f"Assigned task {task_id} to user {user_id}", extra={'category': 'watcher'})

    def _handle_moved_out(self, file_path: str) -> None:
        """Handle file moved out of user folder."""
        user_id = self._file_users.get(file_path)
        if not user_id:
            return

        task_info = self._extract_task_info(file_path)
        if not task_info:
            return

        self.logger.info(f"File moved out of user folder: {file_path}", extra={'category': 'watcher'})
        if self.movement_logger:
            self.movement_logger.info(
                f"Moved out: {file_path} (was user: {user_id})",
                extra={
                    'category': 'movement',
                    'pathname': file_path,
                    'details': {'user_id': user_id}
                }
            )

        task_id = task_info["task_id"]
        if not task_id:
            self.logger.warning(f"Cannot unassign: no task_id in filename {task_info['filename']}", extra={'category': 'watcher'})
            return

        if self.api_client.unassign_task(task_id):
            del self._file_users[file_path]
            self.logger.info(f"Unassigned task {task_id}", extra={'category': 'watcher'})

    def _handle_reassigned(self, dest_path: str) -> None:
        """Handle file moved from one user folder to another (reassign)."""
        src_path = self._reassign_sources.pop(dest_path, None)
        if not src_path:
            return

        src_user_info = self._extract_user_info(src_path)
        dest_user_info = self._extract_user_info(dest_path)
        if not src_user_info or not dest_user_info:
            return

        src_user_id = src_user_info["user_id"]
        dest_user_id = dest_user_info["user_id"]
        src_user_name = src_user_info["user_name"]
        dest_user_name = dest_user_info["user_name"]

        if src_user_id == dest_user_id:
            return

        task_info = self._extract_task_info(dest_path)
        if not task_info:
            return

        task_id = task_info["task_id"]

        self.logger.info(f"File reassigned: {dest_path} (from {src_user_name} to {dest_user_name})", extra={'category': 'watcher'})
        if self.movement_logger:
            self.movement_logger.info(
                f"Reassigned: {dest_path} from user {src_user_name} -> user {dest_user_name}",
                extra={
                    'category': 'movement',
                    'pathname': dest_path,
                    'details': {'src_user_name': src_user_name, 'dest_user_name': dest_user_name}
                }
            )

        if not task_id:
            self.logger.warning(f"Cannot reassign: no task_id in filename {task_info['filename']}", extra={'category': 'watcher'})
            return

        if self.api_client.assign_task(task_ids=[task_id], user_id=dest_user_id):
            self._file_users[dest_path] = dest_user_id
            if src_path in self._file_users:
                del self._file_users[src_path]
            self.logger.info(f"Assigned task {task_id} to {dest_user_name}", extra={'category': 'watcher'})

    def _handle_deleted(self, file_path: str) -> None:
        """Handle file deleted from user folder (reassign only; completion is via completed folder)."""
        filename = os.path.basename(file_path)
        # If file was recently created in another user folder, it's a move (reassign)
        entry = self._recent_user_folder_creates.pop(filename, None)
        if entry:
            _, ts = entry
            if time.time() - ts < self._recent_event_threshold:
                task_info = self._extract_task_info(file_path)
                if task_info:
                    tid = task_info["task_id"]
                    if tid:
                        self.api_client.unassign_task(tid)
                return  # Reassign handled by moved_in; unassign clears old owner

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle file/folder moved event."""
        src_path = event.src_path
        dest_path = event.dest_path

        if not any(str(src_path).startswith(np) for np in self.network_paths):
            return
        if not any(str(dest_path).startswith(np) for np in self.network_paths):
            return

        if event.is_directory:
            self._handle_folder_moved(src_path, dest_path)
            return

        src_in_user = self._is_in_user_folder(src_path)
        dest_in_user = self._is_in_user_folder(dest_path)
        dest_in_completed = self._is_in_completed_folder(dest_path)

        if src_in_user and dest_in_completed:
            self._moved_to_completed_sources[str(dest_path)] = str(src_path)
            self._debouncer.trigger("moved_to_completed", dest_path)
            return

        if not src_in_user and dest_in_user:
            self._moved_in_sources[str(dest_path)] = str(src_path)
            self._debouncer.trigger("moved_in", dest_path)
        elif src_in_user and dest_in_user:
            self._reassign_sources[str(dest_path)] = str(src_path)
            self._debouncer.trigger("reassigned", dest_path)
        elif src_in_user and not dest_in_user:
            self._debouncer.trigger("moved_out", src_path)

    def _handle_folder_moved(self, src_path: str, dest_path: str) -> None:
        """Handle folder moved event."""
        src_in_user = self._is_in_user_folder(src_path)
        dest_in_user = self._is_in_user_folder(dest_path)

        if not src_in_user and dest_in_user:
            dest_user_info = self._extract_user_info(dest_path)
            if dest_user_info:
                dest_user_id = dest_user_info["user_id"]
                dest_user_name = dest_user_info["user_name"]
                self._folder_users[dest_path] = dest_user_id
                self.logger.info(
                    f"Folder moved to user folder: {dest_path} (user: {dest_user_name})",
                    extra={'category': 'watcher'}
                )
                if self.movement_logger:
                    self.movement_logger.info(
                        f"Folder moved in: {dest_path} from {src_path} -> user: {dest_user_name}",
                        extra={
                            'category': 'movement',
                            'pathname': dest_path,
                            'details': {'src_path': src_path, 'user_name': dest_user_name}
                        }
                    )
        elif src_in_user and dest_in_user:
            src_user_info = self._extract_user_info(src_path)
            dest_user_info = self._extract_user_info(dest_path)
            if src_user_info and dest_user_info:
                src_user_id = src_user_info["user_id"]
                dest_user_id = dest_user_info["user_id"]
                src_user_name = src_user_info["user_name"]
                dest_user_name = dest_user_info["user_name"]
                if src_user_id != dest_user_id:
                    self._folder_users[dest_path] = dest_user_id
                    if src_path in self._folder_users:
                        del self._folder_users[src_path]
                    self.logger.info(
                        f"Folder reassigned: {dest_path} (from {src_user_name} to {dest_user_name})",
                        extra={'category': 'watcher'}
                    )
                    if self.movement_logger:
                        self.movement_logger.info(
                            f"Folder reassigned: {dest_path} from {src_user_name} -> {dest_user_name}",
                            extra={
                                'category': 'movement',
                                'pathname': dest_path,
                                'details': {'src_user_name': src_user_name, 'dest_user_name': dest_user_name}
                            }
                        )
        elif src_in_user and not dest_in_user:
            src_user_id = self._folder_users.get(src_path)
            if src_user_id:
                del self._folder_users[src_path]
                self.logger.info(
                    f"Folder moved out of user folder: {src_path} (was user: {src_user_id})",
                    extra={'category': 'watcher'}
                )
                if self.movement_logger:
                    self.movement_logger.info(
                        f"Folder moved out: {src_path} (was user: {src_user_id})",
                        extra={
                            'category': 'movement',
                            'pathname': src_path,
                            'details': {'user_id': src_user_id}
                        }
                    )

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file created event (e.g. move implemented as copy+delete)."""
        if event.is_directory:
            return

        file_path = event.src_path
        if not any(str(file_path).startswith(np) for np in self.network_paths):
            return

        if self._is_in_completed_folder(file_path):
            filename = os.path.basename(file_path)
            entry = self._recent_user_deletes_for_completed.pop(filename, None)
            if entry:
                src_path, ts = entry
                if time.time() - ts < self._recent_event_threshold:
                    self._moved_to_completed_sources[str(file_path)] = str(src_path)
                    self._debouncer.trigger("moved_to_completed", file_path)
            return

        if self._is_in_user_folder(file_path):
            filename = os.path.basename(file_path)
            now = time.time()
            self._recent_user_folder_creates[filename] = (str(file_path), now)
            self._recent_user_folder_creates = {
                k: v
                for k, v in self._recent_user_folder_creates.items()
                if now - v[1] < self._cleanup_interval
            }
            self._debouncer.trigger("moved_in", file_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        """Handle file/folder deleted event."""
        file_path = event.src_path

        if not any(str(file_path).startswith(np) for np in self.network_paths):
            return

        filename = os.path.basename(file_path)

        # Handle zip file deletion
        if filename.endswith(".zip"):
            self._handle_zip_deleted(file_path)
            return

        if event.is_directory:
            return

        if self._is_in_user_folder(file_path):
            cre = self._recent_user_folder_creates.get(filename)
            if not (
                cre
                and time.time() - cre[1] < self._recent_event_threshold
            ):
                now = time.time()
                self._recent_user_deletes_for_completed[filename] = (file_path, now)
                self._recent_user_deletes_for_completed = {
                    k: v
                    for k, v in self._recent_user_deletes_for_completed.items()
                    if now - v[1] < self._cleanup_interval
                }
            self._debouncer.trigger("deleted", file_path)
        else:
            self._recent_artwork_deletions[filename] = (str(file_path), time.time())

    def _handle_zip_deleted(self, file_path: str) -> None:
        """Handle zip deleted from user folder (completion is via completed folder)."""
        user_info = self._extract_user_info(file_path)
        if not user_info:
            return

        user_id = user_info["user_id"]
        user_name = user_info["user_name"]
        filename = os.path.basename(file_path)

        if not filename.endswith(".zip"):
            return

        name_without_ext = filename[:-4]
        parts = name_without_ext.split("-")
        if len(parts) < 2:
            self.logger.warning(f"Cannot parse zip filename: {filename}", extra={'category': 'watcher'})
            return

        task_id = parts[0]

        if self._is_recently_completed_task(task_id):
            return

        moved_out_key = f"moved_out:{file_path}"
        if moved_out_key in self._debouncer._pending_events:
            self.logger.info(
                f"Zip moved from user folder (moved_out pending): {file_path} -> skip, unassign will handle",
                extra={'category': 'watcher'},
            )
            return

        if self._pending_moved_to_completed_for_basename(filename):
            self.logger.info(
                f"Zip move to completed pending: {file_path} -> skip zip_deleted handling",
                extra={'category': 'watcher'},
            )
            return

        if self.completed_paths and self._find_file_in_roots(filename, self.completed_paths):
            self.logger.info(
                f"Zip present in completed folder after move: {file_path} (task: {task_id})",
                extra={'category': 'watcher'},
            )
            if self.movement_logger:
                self.movement_logger.info(
                    f"Zip completed (in completed folder): {file_path} (user: {user_id}, task: {task_id})",
                    extra={
                        'category': 'movement',
                        'pathname': file_path,
                        'details': {'user_id': user_id, 'task_id': task_id},
                    },
                )
            if user_id and self.api_client.complete_task(task_id, user_id):
                if file_path in self._file_users:
                    del self._file_users[file_path]
                self._record_recent_completed_task(task_id)
                self.logger.info(
                    f"Completed task {task_id} by user {user_name}", extra={'category': 'watcher'}
                )
            return

        if self._find_file_in_roots(filename, self._artwork_search_roots):
            self.logger.info(
                f"Zip moved from user folder to artworks: {file_path} -> unassigning task {task_id}",
                extra={'category': 'watcher'},
            )
            if self.movement_logger:
                self.movement_logger.info(
                    f"Zip moved out (unassign): {file_path} (was user: {user_id}, task: {task_id})",
                    extra={
                        'category': 'movement',
                        'pathname': file_path,
                        'details': {'user_id': user_id, 'task_id': task_id},
                    },
                )
            if self.api_client.unassign_task(task_id):
                if file_path in self._file_users:
                    del self._file_users[file_path]
                self.logger.info(
                    f"Unassigned task {task_id} (file moved to artworks)", extra={'category': 'watcher'}
                )
            return

        now = time.time()
        self._recent_user_deletes_for_completed[filename] = (file_path, now)
        self._recent_user_deletes_for_completed = {
            k: v
            for k, v in self._recent_user_deletes_for_completed.items()
            if now - v[1] < self._cleanup_interval
        }
        self.logger.info(
            f"Zip deleted from user folder: {file_path} (user: {user_name}, task: {task_id})",
            extra={'category': 'watcher'},
        )

    def process_pending(self) -> None:
        """Process pending debounced events."""
        self._debouncer.process_pending()


class FileWatcher:
    """File system watcher for hot folders."""

    def __init__(
        self,
        api_client: APIClient,
        network_paths: List[str],
        user_paths: Optional[List[str]] = None,
        completed_paths: Optional[List[str]] = None,
        movement_log_dir: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        debounce_seconds: float = 2.0,
        recent_event_threshold: int = 15,
        cleanup_interval: int = 30,
        posthog_config: Optional[Dict] = None,
    ):
        self.api_client = api_client
        self.network_paths = network_paths
        self.user_paths = user_paths or []
        self.completed_paths = completed_paths or []
        self.movement_log_dir = movement_log_dir
        self.logger = logger or logging.getLogger(__name__)
        self.debounce_seconds = debounce_seconds
        self._recent_event_threshold = recent_event_threshold
        self._cleanup_interval = cleanup_interval
        self.posthog_config = posthog_config

        from watchdog.observers import Observer as WatcherObserver

        self.observer: Optional[WatcherObserver] = None
        self.handler: Optional[HotFolderEventHandler] = None
        self._running = False

    def _setup_movement_logger(self) -> Optional[logging.Logger]:
        """Set up a dedicated logger for file movement events."""
        import socket
        
        if not self.movement_log_dir:
            return None
        movement_logger = logging.getLogger("print_agent_movement")
        movement_logger.setLevel(logging.INFO)
        movement_logger.handlers = []

        os.makedirs(self.movement_log_dir, exist_ok=True)

        file_handler = logging.FileHandler(
            os.path.join(self.movement_log_dir, "movements.log"), encoding="utf-8"
        )
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        file_handler.setFormatter(formatter)
        movement_logger.addHandler(file_handler)

        # Add PostHog handler
        if self.posthog_config:
            from src.health_reporting import PostHogHandler
            posthog_handler = PostHogHandler(**self.posthog_config)
            posthog_handler.setLevel(logging.INFO)
            movement_logger.addHandler(posthog_handler)

        return movement_logger

    def start(self) -> None:
        """Start watching network paths."""
        if self._running:
            return

        self.logger.info(f"Starting file watcher for {len(self.network_paths)} network paths", extra={'category': 'watcher'})

        movement_logger = self._setup_movement_logger()

        # Create event handler
        self.handler = HotFolderEventHandler(
            self.api_client,
            self.network_paths,
            user_paths=self.user_paths,
            completed_paths=self.completed_paths,
            logger=self.logger,
            movement_logger=movement_logger,
            debounce_seconds=self.debounce_seconds,
            recent_event_threshold=self._recent_event_threshold,
            cleanup_interval=self._cleanup_interval,
        )

        # Create and start observer
        from watchdog.observers import Observer as WatcherObserver

        observer: WatcherObserver = WatcherObserver()
        self.observer = observer

        for path in self.network_paths:
            if os.path.exists(path):
                observer.schedule(self.handler, path, recursive=True)
                self.logger.info(f"Watching: {path}", extra={'category': 'watcher'})
            else:
                self.logger.warning(f"Network path does not exist: {path}", extra={'category': 'watcher'})

        observer.start()
        self._running = True

    def stop(self) -> None:
        """Stop watching."""
        if not self._running:
            return

        self.logger.info("Stopping file watcher", extra={'category': 'watcher'})

        if self.observer:
            self.observer.stop()
            self.observer.join()

        self._running = False

    def process_events(self) -> None:
        """Process pending events. Call periodically."""
        if self.handler:
            self.handler.process_pending()

    def is_pending_artwork_move(self, filename: str) -> bool:
        """Return True if filename was recently deleted from artwork (move in progress)."""
        if not self.handler:
            return False
        return self.handler.is_pending_artwork_move(filename)

    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._running
