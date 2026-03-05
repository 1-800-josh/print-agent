"""Order synchronization module for Print Agent."""

import logging
import os
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from multiprocessing import cpu_count
from typing import Callable, Dict, List, Optional, Tuple

import requests

from src.api_client import APIClient, PrintingTask
from src.config import AgentConfig
from src.utils import generate_filename


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool
    downloaded: int
    failed: int
    errors: List[str]


class OrderSync:
    """Synchronizes orders from API to network paths."""

    def __init__(
        self,
        config: AgentConfig,
        api_client: APIClient,
        logger: Optional[logging.Logger] = None,
        file_exists_in_users: Optional[Callable[[str], bool]] = None,
    ):
        self.config = config
        self.api_client = api_client
        self.logger = logger or logging.getLogger(__name__)
        self._file_exists_in_users = file_exists_in_users
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": self.config.API_KEY})
        self._rename_logger = self._setup_rename_logger()

    def _setup_rename_logger(self) -> logging.Logger:
        """Set up a dedicated logger for rename operations."""
        rename_logger = logging.getLogger("print_agent_rename")
        rename_logger.setLevel(logging.INFO)
        rename_logger.handlers = []

        os.makedirs(self.config.RENAME_LOG_DIR, exist_ok=True)

        file_handler = logging.FileHandler(
            os.path.join(self.config.RENAME_LOG_DIR, "renames.log"), encoding="utf-8"
        )
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        file_handler.setFormatter(formatter)
        rename_logger.addHandler(file_handler)

        return rename_logger

    def _log_rename(self, old_name: str, new_name: str, folder: str) -> None:
        """Log a file rename operation."""
        self._rename_logger.info(f"Renamed: {old_name} -> {new_name} (folder: {folder})")

    def fetch_ready_orders(self) -> List[PrintingTask]:
        """Fetch all READY_FOR_PRODUCTION orders with configured network paths."""
        response = self.api_client.fetch_tasks()

        tasks = response.tasks

        # Filter tasks with network paths (network_path is now integrated into materials)
        filtered_tasks = [task for task in tasks if task.status == "PENDING" and task.network_path]

        self.logger.info(
            f"Filtered {len(filtered_tasks)} tasks with network paths from {len(tasks)} total tasks"
        )
        return filtered_tasks

    def group_by_material_and_date(
        self, tasks: List[PrintingTask]
    ) -> Dict[Tuple[str, str], List[PrintingTask]]:
        """Group tasks by material network path and delivery date."""
        grouped = defaultdict(list)
        for task in tasks:
            key = (task.network_path or task.material_name, task.delivery_date)
            grouped[key].append(task)
        return dict(grouped)

    def download_artwork(
        self,
        artwork_data: Tuple[
            str, str, str, str, str, str
        ],  # url, save_path, order_id, artwork_id, task_id, zip_filename
    ) -> Tuple[bool, str]:
        """Download a single artwork. Designed for multiprocessing."""
        url, save_path, order_id, artwork_id, task_id, zip_filename = artwork_data

        try:
            response = self.session.get(
                url, timeout=self.config.DOWNLOAD_TIMEOUT_SECONDS, stream=True
            )
            response.raise_for_status()

            # Ensure directory exists
            os.makedirs(os.path.dirname(save_path), exist_ok=True)

            with open(save_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            return (True, f"Downloaded {order_id}/{artwork_id}")

        except Exception as e:
            return (False, f"Failed to download {order_id}/{artwork_id}: {e}")

    def sync_orders(self) -> SyncResult:
        """Synchronize orders to network paths."""
        self.logger.info("Starting order sync")

        try:
            tasks = self.fetch_ready_orders()
        except Exception as e:
            self.logger.error(f"Failed to fetch tasks: {e}")
            return SyncResult(success=False, downloaded=0, failed=0, errors=[str(e)])

        if not tasks:
            self.logger.info("No tasks to sync")
            return SyncResult(success=True, downloaded=0, failed=0, errors=[])

        # Use artwork path from config
        artwork_path_config = self.config.ARTWORK_FOLDER

        # Log the artwork network path being used
        if artwork_path_config:
            self.logger.info(f"Using artwork network path: {artwork_path_config}")

        # Group by material and delivery date
        grouped = self.group_by_material_and_date(tasks)

        # Prepare download list
        # Target pattern: {NETWORK_DRIVE_PREFIX}/{artworkNetworkPath}/{material_networkPath}-{date}/{filename}.ext
        prefix = (self.config.NETWORK_DRIVE_PREFIX or "").rstrip(os.sep)
        artwork_path = (artwork_path_config or "").strip().lstrip(os.sep)

        downloads = []
        for (material_network_path, delivery_date), material_tasks in grouped.items():
            # Build base: {NETWORK_DRIVE_PREFIX}/{artworkNetworkPath} (strip leading material if present)
            path_for_material = artwork_path
            if path_for_material and material_network_path:
                for sep in (os.sep, "/", "\\"):
                    material_prefix = f"{material_network_path}{sep}"
                    if path_for_material.startswith(material_prefix):
                        path_for_material = path_for_material[len(material_prefix) :]
                        break
            if prefix:
                base_path = os.path.join(prefix, path_for_material) if path_for_material else prefix
            else:
                base_path = path_for_material or ""
                if not base_path:
                    self.logger.warning(
                        "NETWORK_DRIVE_PREFIX and artwork path are unset; files may save to current directory"
                    )

            # Format delivery date to YYYYMMDD (handle ISO format with time)
            try:
                dt = datetime.fromisoformat(delivery_date.replace("Z", "+00:00"))
                date_formatted = dt.strftime("%Y%m%d")
            except (ValueError, AttributeError):
                date_formatted = delivery_date.replace("-", "").replace(":", "").split("T")[0]

            # Create folder: {base_path}/{material_networkPath}_{YYYYMMDD}/
            folder_name = f"{material_network_path}_{date_formatted}"
            date_folder = os.path.join(base_path, folder_name)

            for task in material_tasks:
                if not task.morse_code:
                    continue

                task_has_multiple_artworks = len(task.artworks) > 1
                zip_filename = None

                # For multi-artwork tasks, check if zip already exists before processing
                if task_has_multiple_artworks:
                    zip_filename = f"{task.task_id}-{task.order_id}.zip"
                    zip_path = os.path.join(date_folder, zip_filename)
                    if os.path.exists(zip_path):
                        self.logger.debug(f"Skipping existing zip in artworks: {zip_path}")
                        continue
                    # Also check in user folders
                    if self._file_exists_in_users and self._file_exists_in_users(zip_filename):
                        self.logger.debug(f"Skipping existing zip in user folder: {zip_filename}")
                        continue

                for idx, artwork in enumerate(task.artworks):
                    if not artwork.uploadthing_url:
                        continue

                    option_values = (
                        artwork.option_values if hasattr(artwork, "option_values") else []
                    )
                    artwork_group_id = artwork.artwork_group_id or ""

                    filename_base = generate_filename(
                        task.order_id,
                        artwork_group_id,
                        option_values,
                        task.morse_code,
                        task.task_id,
                        idx + 1 if task_has_multiple_artworks else 0,
                    )

                    ext = os.path.splitext(artwork.uploadthing_url)[1] or ".png"

                    filename = f"{filename_base}{ext}"

                    save_path = os.path.join(date_folder, filename)

                    if os.path.exists(save_path):
                        self.logger.debug(f"Skipping existing file: {save_path}")
                        continue

                    if self._file_exists_in_users and self._file_exists_in_users(filename):
                        self.logger.debug(f"Skipping file in user folder: {filename}")
                        continue

                    self._log_rename(
                        os.path.basename(artwork.uploadthing_url), filename, date_folder
                    )

                    downloads.append(
                        (
                            artwork.uploadthing_url,
                            save_path,
                            task.order_id,
                            artwork.artwork_group_id,
                            task.task_id,
                            zip_filename,
                        )
                    )

        if not downloads:
            self.logger.info("No artworks to download")
            return SyncResult(success=True, downloaded=0, failed=0, errors=[])

        self.logger.info(
            f"Downloading {len(downloads)} artworks using {self.config.MAX_WORKERS} workers"
        )

        # Download using process pool
        downloaded = 0
        failed = 0
        errors = []
        task_zip_files: Dict[str, List[str]] = defaultdict(list)  # zip_path -> list of file paths

        with ThreadPoolExecutor(max_workers=min(self.config.MAX_WORKERS, cpu_count())) as executor:
            futures = {
                executor.submit(self.download_artwork, download): download for download in downloads
            }

            for future in as_completed(futures):
                success, message = future.result()
                download = futures[future]
                if success:
                    downloaded += 1
                    self.logger.debug(message)
                    # Track files for zipping
                    if download[5]:  # zip_filename
                        zip_path = os.path.join(os.path.dirname(download[1]), download[5])
                        task_zip_files[zip_path].append(download[1])
                else:
                    failed += 1
                    errors.append(message)
                    self.logger.error(message)

        # Create zip files for multi-artwork tasks
        for zip_path, file_paths in task_zip_files.items():
            if len(file_paths) > 1:
                try:
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                        for file_path in file_paths:
                            if os.path.exists(file_path):
                                zipf.write(file_path, os.path.basename(file_path))
                    self.logger.info(f"Created zip: {zip_path} with {len(file_paths)} files")
                    # Delete individual files after zipping
                    for file_path in file_paths:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            self.logger.debug(f"Deleted original file: {file_path}")
                except Exception as e:
                    self.logger.error(f"Failed to create zip {zip_path}: {e}")
                    errors.append(f"Failed to create zip {zip_path}: {e}")

        self.logger.info(f"Sync complete: {downloaded} downloaded, {failed} failed")

        return SyncResult(
            success=failed == 0,
            downloaded=downloaded,
            failed=failed,
            errors=errors,
        )
