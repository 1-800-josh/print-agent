"""Tests for order sync."""

import os
import pytest
from unittest.mock import Mock, patch

from src.api_client import APIClient, PrintingTask, ArtworkInfo, TasksResponse, User
from src.config import AgentConfig
from src.order_sync import OrderSync, SyncResult


class TestGroupByMaterialAndDate:
    """Test group_by_material_and_date."""

    @pytest.fixture
    def order_sync(self):
        config = AgentConfig(API_KEY="test")
        return OrderSync(config=config, api_client=Mock())

    @pytest.fixture
    def sample_task(self):
        return PrintingTask(
            task_id="task1",
            order_id="order1",
            material_name="Vinyl",
            delivery_date="2024-03-15",
            status="PENDING",
            network_path="vinyl_path",
            artworks=[
                ArtworkInfo(
                    artwork_group_id="ag1",
                    image_id="img1",
                    artwork_id="a1",
                    uploadthing_url="https://example.com/img.png",
                )
            ],
            morse_code="morse123",
        )

    def test_same_material_and_date_grouped_together(self, order_sync, sample_task):
        """Two tasks with same (material, date) end up in one group."""
        task2 = PrintingTask(
            task_id="task2",
            order_id="order2",
            material_name="Vinyl",
            delivery_date="2024-03-15",
            status="PENDING",
            network_path="vinyl_path",
            artworks=[],
            morse_code="morse456",
        )
        grouped = order_sync.group_by_material_and_date([sample_task, task2])
        key = ("vinyl_path", "2024-03-15")
        assert key in grouped
        assert len(grouped[key]) == 2

    def test_different_materials_separate_groups(self, order_sync, sample_task):
        """Two tasks with different materials end up in separate groups."""
        task2 = PrintingTask(
            task_id="task2",
            order_id="order2",
            material_name="Paper",
            delivery_date="2024-03-15",
            status="PENDING",
            network_path="paper_path",
            artworks=[],
            morse_code="morse456",
        )
        grouped = order_sync.group_by_material_and_date([sample_task, task2])
        assert len(grouped) == 2
        assert ("vinyl_path", "2024-03-15") in grouped
        assert ("paper_path", "2024-03-15") in grouped

    def test_uses_network_path_when_present(self, order_sync):
        """Grouping key uses network_path when present."""
        task = PrintingTask(
            task_id="t1",
            order_id="o1",
            material_name="Vinyl",
            delivery_date="2024-03-15",
            status="PENDING",
            network_path="custom_path",
            artworks=[],
        )
        grouped = order_sync.group_by_material_and_date([task])
        assert ("custom_path", "2024-03-15") in grouped

    def test_uses_material_name_when_network_path_none(self, order_sync):
        """Grouping key uses material_name when network_path is None."""
        task = PrintingTask(
            task_id="t1",
            order_id="o1",
            material_name="Vinyl",
            delivery_date="2024-03-15",
            status="PENDING",
            network_path=None,
            artworks=[],
        )
        grouped = order_sync.group_by_material_and_date([task])
        assert ("Vinyl", "2024-03-15") in grouped


class TestShouldSkipArtworkDownload:
    """Test should_skip_artwork_download skip logic in sync_orders."""

    @pytest.fixture
    def config(self, tmp_path):
        return AgentConfig(
            API_KEY="test",
            NETWORK_DRIVE_PREFIX=str(tmp_path),
            ARTWORK_FOLDER="artworks",
            RENAME_LOG_DIR=str(tmp_path / "renames"),
        )

    @pytest.fixture
    def task_with_artwork(self):
        return PrintingTask(
            task_id="task1",
            order_id="order1",
            material_name="vinyl_path",
            delivery_date="2024-03-15",
            status="PENDING",
            network_path="vinyl_path",
            artworks=[
                ArtworkInfo(
                    artwork_group_id="ag1",
                    image_id="img1",
                    artwork_id="a1",
                    uploadthing_url="https://example.com/image.png",
                    option_values=[],
                )
            ],
            morse_code="morse123",
        )

    @patch("src.order_sync.OrderSync.download_artwork")
    def test_file_skipped_when_callback_returns_true(
        self, mock_download, config, task_with_artwork
    ):
        """When should_skip_artwork_download returns True, file is not downloaded."""
        mock_api = Mock()
        mock_api.fetch_tasks.return_value = TasksResponse(tasks=[task_with_artwork])
        mock_api.fetch_users.return_value = []

        def skip_fn(filename):
            return filename == "task1-order1-morse123.png"

        order_sync = OrderSync(
            config=config,
            api_client=mock_api,
            file_exists_in_users=lambda _: False,
            should_skip_artwork_download=skip_fn,
        )

        result = order_sync.sync_orders()

        assert mock_download.call_count == 0
        assert result.downloaded == 0

    @patch("src.order_sync.OrderSync.download_artwork")
    def test_file_downloaded_when_callback_returns_false(
        self, mock_download, config, task_with_artwork
    ):
        """When should_skip_artwork_download returns False, file is downloaded."""
        mock_download.return_value = (True, "Downloaded")

        mock_api = Mock()
        mock_api.fetch_tasks.return_value = TasksResponse(tasks=[task_with_artwork])
        mock_api.fetch_users.return_value = []

        order_sync = OrderSync(
            config=config,
            api_client=mock_api,
            file_exists_in_users=lambda _: False,
            should_skip_artwork_download=lambda _: False,
        )

        result = order_sync.sync_orders()

        assert mock_download.call_count == 1
        assert result.downloaded == 1

    @patch("src.order_sync.OrderSync.download_artwork")
    def test_assigned_task_downloads_to_user_folder(
        self, mock_download, config, task_with_artwork
    ):
        """When task has assigned_user_id, save under that user's folder, not artworks."""
        mock_download.return_value = (True, "Downloaded")

        mock_api = Mock()
        mock_api.fetch_tasks.return_value = TasksResponse(tasks=[task_with_artwork])
        mock_api.fetch_users.return_value = [
            User(user_id="99", first_name="Jane", last_name="Doe"),
        ]
        task_with_artwork.assigned_user_id = "99"

        order_sync = OrderSync(
            config=config,
            api_client=mock_api,
            file_exists_in_users=lambda _: False,
            should_skip_artwork_download=lambda _: False,
        )

        result = order_sync.sync_orders()

        assert mock_download.call_count == 1
        assert result.downloaded == 1
        save_path = mock_download.call_args[0][0][1]
        norm = save_path.replace(os.sep, "/")
        assert "users" in norm and "99-Jane Doe" in norm
        assert "artworks" not in norm
