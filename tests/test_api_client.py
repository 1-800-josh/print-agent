"""Tests for API client."""

import pytest
from unittest.mock import Mock, patch, call
from src.api_client import (
    APIClient,
    PrintingTask,
    ArtworkInfo,
    TasksResponse,
    AgentConfigResponse,
    User,
)


class TestAPIClient:
    """Test API client functionality."""

    @pytest.fixture
    def client(self):
        return APIClient(
            base_url="http://test.com",
            api_key="test-key",
            organisation_id="org-123",
            uploadthing_app_id="test-app",
        )

    @patch("src.api_client.requests.Session")
    def test_fetch_tasks(self, mock_session_class, client):
        """Test fetching tasks from API."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        client.session = mock_session

        mock_response = Mock()
        mock_response.json.return_value = {
            "tasks": [
                {
                    "id": "task-1",
                    "orderId": "order-1",
                    "relatedMaterialName": "Vinyl",
                    "deliveryDate": "2024-03-15",
                    "taskState": "PENDING",
                    "networkPath": "//server/share",
                    "artworks": [
                        {
                            "artwork_group_id": "ag-1",
                            "image_id": "img-1",
                            "id": "artwork-1",
                            "option_values": ["A", "B"],
                        }
                    ],
                    "morseCode": "morse123",
                }
            ],
            "artworkNetworkPath": "//server/share/artworks",
            "userNetworkPath": "//server/share/users",
        }
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        response = client.fetch_tasks()

        assert isinstance(response, TasksResponse)
        assert len(response.tasks) == 1
        assert response.tasks[0].task_id == "task-1"
        assert response.tasks[0].order_id == "order-1"
        assert response.tasks[0].network_path == "//server/share"
        assert response.artwork_network_path == "//server/share/artworks"
        assert response.user_network_path == "//server/share/users"
        mock_session.get.assert_called_once_with(
            "http://test.com/api/organisations/org-123/print-agent/printing-tasks",
            timeout=30,
        )

    @patch("src.api_client.requests.Session")
    def test_fetch_agent_config(self, mock_session_class, client):
        """Test fetching agent config from API."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        client.session = mock_session

        mock_response = Mock()
        mock_response.json.return_value = {
            "artworksNetworkPath": "artworks",
            "usersNetworkPath": "users",
        }
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        response = client.fetch_agent_config()

        assert isinstance(response, AgentConfigResponse)
        assert response.artwork_network_path == "artworks"
        assert response.user_network_path == "users"
        mock_session.get.assert_called_once_with(
            "http://test.com/api/organisations/org-123/print-agent/agent-config",
            timeout=30,
        )

    @patch("src.api_client.requests.Session")
    def test_fetch_users(self, mock_session_class, client):
        """Test fetching users from API."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        client.session = mock_session

        mock_response = Mock()
        mock_response.json.return_value = [
            {"id": "user-1", "firstName": "Jane", "lastName": "Doe"},
            {"id": "user-2", "firstName": "Mike", "lastName": "Miller"},
        ]
        mock_response.raise_for_status = Mock()
        mock_session.get.return_value = mock_response

        response = client.fetch_users()

        assert len(response) == 2
        assert isinstance(response[0], User)
        assert response[0].user_id == "user-1"
        assert response[0].first_name == "Jane"
        assert response[0].last_name == "Doe"
        assert response[1].user_id == "user-2"
        assert response[1].first_name == "Mike"
        assert response[1].last_name == "Miller"
        mock_session.get.assert_called_once_with(
            "http://test.com/api/organisations/org-123/print-agent/users",
            timeout=30,
        )

    @patch("src.api_client.requests.Session")
    def test_assign_task(self, mock_session_class, client):
        """Test assigning a task."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        client.session = mock_session

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_session.post.return_value = mock_response

        result = client.assign_task(
            task_ids=["task-1"],
            user_id="user-1",
        )

        assert result is True
        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args[1]
        assert call_kwargs["json"]["task_ids"] == ["task-1"]
        assert call_kwargs["json"]["user_id"] == "user-1"

    @patch("src.api_client.requests.Session")
    def test_complete_task(self, mock_session_class, client):
        """Test completing a task."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        client.session = mock_session

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_session.post.return_value = mock_response

        result = client.complete_task("task-1", "user-1")

        assert result is True
        mock_session.post.assert_called_once()
