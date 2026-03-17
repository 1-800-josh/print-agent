"""API client for Print Agent."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.health_reporting import emit_status_event


@dataclass
class ArtworkInfo:
    """Artwork information for a task."""

    artwork_group_id: str
    image_id: str
    artwork_id: str
    uploadthing_url: str
    option_values: List[str] = field(default_factory=list)


@dataclass
class PrintingTask:
    """A printing task from the API."""

    task_id: str
    order_id: str
    material_name: str
    delivery_date: str
    status: str
    network_path: Optional[str]
    artworks: List[ArtworkInfo] = field(default_factory=list)
    morse_code: Optional[str] = None
    assigned_user_id: Optional[str] = None  # from internalUserId


@dataclass
class User:
    """A user from the internal portal."""

    user_id: str
    first_name: str
    last_name: str


@dataclass
class TasksResponse:
    """Response from the tasks API endpoint."""

    tasks: List[PrintingTask]


class APIClient:
    """Client for interacting with the Print Agent API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        organisation_id: str,
        uploadthing_app_id: str,
        logger: Optional[logging.Logger] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.organisation_id = organisation_id
        self.uploadthing_app_id = uploadthing_app_id
        self.logger = logger or logging.getLogger(__name__)

        # Create session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # Set headers
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "x-api-key": api_key,
            }
        )

    def _get_url(self, path: str) -> str:
        """Get full URL for API endpoint."""
        return f"{self.base_url}{path}"

    def _parse_response(self, response: requests.Response, expected_type: str = "dict") -> Any:
        """Parse and normalize API response.

        Handles common API envelope variants:
        - {"data":[...]}
        - {"json":[...]}
        - {"json":{"data":[...]}}
        """
        data = response.json()

        if isinstance(data, str):
            data = json.loads(data)

        payload = data.get("json", data)
        if isinstance(payload, str):
            payload = json.loads(payload)

        if expected_type == "dict" and not isinstance(payload, dict):
            raise ValueError(
                f"API returned unexpected response type {type(payload).__name__}, expected dict. "
                f"Response: {repr(data)[:200]}"
            )

        return payload

    def fetch_tasks(self) -> TasksResponse:
        """Fetch READY_FOR_PRODUCTION printing tasks."""
        url = self._get_url(f"/api/organisations/{self.organisation_id}/print-agent/printing-tasks")

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            payload = self._parse_response(response, expected_type="list")

            if isinstance(payload, list):
                tasks_raw = payload
            elif isinstance(payload, dict):
                tasks_raw = payload.get("tasks", [])
            else:
                tasks_raw = []
            tasks = []
            for item in tasks_raw:
                artworks = []
                for artwork_data in item.get("artworks", []):
                    image_id = (
                        artwork_data.get("image_id")
                        or artwork_data.get("imageId")
                        or artwork_data.get("id")
                        or ""
                    )
                    artwork_path = artwork_data.get("path") or ""
                    if isinstance(artwork_path, str) and artwork_path:
                        if artwork_path.startswith("http://") or artwork_path.startswith(
                            "https://"
                        ):
                            uploadthing_url = artwork_path
                        else:
                            uploadthing_url = (
                                f"https://{self.uploadthing_app_id}.ufs.sh/f/{artwork_path}"
                            )
                    else:
                        uploadthing_url = f"https://{self.uploadthing_app_id}.ufs.sh/f/{image_id}"
                    artworks.append(
                        ArtworkInfo(
                            artwork_group_id=artwork_data.get("artwork_group_id", ""),
                            image_id=image_id,
                            artwork_id=str(artwork_data.get("id", "")),
                            uploadthing_url=uploadthing_url,
                            option_values=artwork_data.get("option_values", []),
                        )
                    )

                uid = item.get("internalUserId")
                assigned_user_id = str(uid) if uid is not None else None

                tasks.append(
                    PrintingTask(
                        task_id=item.get("id", ""),
                        order_id=item.get("orderId", ""),
                        material_name=item.get("relatedMaterialName", ""),
                        delivery_date=item.get("deliveryDate", ""),
                        status=item.get("taskState", ""),
                        network_path=item.get("networkPath"),
                        artworks=artworks,
                        morse_code=item.get("morseCode"),
                        assigned_user_id=assigned_user_id,
                    )
                )

            self.logger.info(f"Fetched {len(tasks)} tasks from API")
            return TasksResponse(tasks=tasks)

        except Exception as e:
            self.logger.error(f"Failed to fetch tasks: {e}")
            emit_status_event(
                "api_error",
                level="error",
                state="degraded",
                healthy=False,
                details={"operation": "fetch_tasks", "error": str(e), "url": url},
            )
            raise

    def assign_task(
        self,
        task_ids: List[str],
        user_id: Optional[str] = None,
    ) -> bool:
        """Assign task(s) to a user.

        API expects { task_ids: Array<string>; user_id?: string }.
        task_ids cannot be empty.
        """
        url = self._get_url(
            f"/api/organisations/{self.organisation_id}/print-agent/printing-tasks/assign"
        )

        if not task_ids:
            self.logger.error("assign_task requires non-empty task_ids")
            return False

        payload: Dict[str, Any] = {"task_ids": task_ids}
        if user_id:
            payload["user_id"] = user_id

        try:
            response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            self.logger.info(f"Assigned task(s) {task_ids} to user {user_id}")
            return True

        except requests.RequestException as e:
            self.logger.error(f"Failed to assign {task_ids}: {e}")
            emit_status_event(
                "api_error",
                level="error",
                state="degraded",
                healthy=False,
                details={
                    "operation": "assign_task",
                    "task_ids": task_ids,
                    "user_id": user_id,
                    "error": str(e),
                    "url": url,
                },
            )
            return False

    def unassign_task(self, task_id: str) -> bool:
        """Unassign a task."""
        url = self._get_url(
            f"/api/organisations/{self.organisation_id}/print-agent/printing-tasks/unassign"
        )

        try:
            response = self.session.post(url, json={"task_ids": [task_id]}, timeout=30)
            response.raise_for_status()
            self.logger.info(f"Unassigned task {task_id}")
            return True

        except requests.RequestException as e:
            self.logger.error(f"Failed to unassign task {task_id}: {e}")
            emit_status_event(
                "api_error",
                level="error",
                state="degraded",
                healthy=False,
                details={
                    "operation": "unassign_task",
                    "task_id": task_id,
                    "error": str(e),
                    "url": url,
                },
            )
            return False

    def complete_task(self, task_id: str, user_id: str) -> bool:
        """Mark a task as completed."""
        url = self._get_url(
            f"/api/organisations/{self.organisation_id}/print-agent/printing-tasks/complete"
        )

        try:
            response = self.session.post(
                url, json={"task_ids": [task_id], "user_id": user_id}, timeout=30
            )
            response.raise_for_status()
            self.logger.info(f"Completed task {task_id} by user {user_id}")
            return True

        except requests.RequestException as e:
            self.logger.error(f"Failed to complete task {task_id}: {e}")
            emit_status_event(
                "api_error",
                level="error",
                state="degraded",
                healthy=False,
                details={
                    "operation": "complete_task",
                    "task_id": task_id,
                    "user_id": user_id,
                    "error": str(e),
                    "url": url,
                },
            )
            return False

    def fetch_users(self) -> List[User]:
        """Fetch all internal users from internal portal."""
        url = self._get_url(f"/api/organisations/{self.organisation_id}/print-agent/users")

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            payload = self._parse_response(response, expected_type="list")

            if isinstance(payload, list):
                users_data = payload
            elif isinstance(payload, dict):
                users_data = payload.get("users", [])
            else:
                users_data = []

            users = []
            for item in users_data:
                users.append(
                    User(
                        user_id=item.get("id", ""),
                        first_name=item.get("firstName", ""),
                        last_name=item.get("lastName", ""),
                    )
                )

            self.logger.info(f"Fetched {len(users)} users from API")
            return users

        except Exception as e:
            self.logger.error(f"Failed to fetch users: {e}")
            emit_status_event(
                "api_error",
                level="error",
                state="degraded",
                healthy=False,
                details={"operation": "fetch_users", "error": str(e), "url": url},
            )
            raise
