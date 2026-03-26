"""Tests for file watcher."""

import os
import pytest
from unittest.mock import Mock, patch

from src.file_watcher import HotFolderEventHandler, FileWatcher


class TestIsPendingArtworkMove:
    """Test is_pending_artwork_move."""

    @pytest.fixture
    def handler(self):
        return HotFolderEventHandler(
            api_client=Mock(),
            network_paths=["/network"],
            user_paths=["/network/users"],
            completed_paths=[],
            recent_event_threshold=15,
        )

    def test_returns_true_when_filename_recently_deleted(self, handler):
        """Returns True when filename is in _recent_artwork_deletions within threshold."""
        handler._recent_artwork_deletions["task1-ord1-morse.png"] = ("/artwork/file.png", 100.0)
        with patch("src.file_watcher.time") as mock_time:
            mock_time.time.return_value = 105.0  # 5 seconds later, within 15s
            assert handler.is_pending_artwork_move("task1-ord1-morse.png") is True

    def test_returns_false_when_filename_not_in_dict(self, handler):
        """Returns False when filename is not in _recent_artwork_deletions."""
        handler._recent_artwork_deletions["other.png"] = ("/artwork/other.png", 100.0)
        assert handler.is_pending_artwork_move("task1-ord1-morse.png") is False

    def test_returns_false_when_timestamp_exceeds_threshold(self, handler):
        """Returns False when filename was deleted longer than threshold ago."""
        handler._recent_artwork_deletions["task1-ord1-morse.png"] = ("/artwork/file.png", 100.0)
        with patch("src.file_watcher.time") as mock_time:
            mock_time.time.return_value = 120.0  # 20 seconds later, exceeds 15s
            assert handler.is_pending_artwork_move("task1-ord1-morse.png") is False


class TestExtractTaskInfo:
    """Test _extract_task_info."""

    @pytest.fixture
    def handler(self):
        return HotFolderEventHandler(
            api_client=Mock(),
            network_paths=["/network"],
            user_paths=[],
            completed_paths=[],
        )

    def test_valid_filename_single_image(self, handler):
        """Extracts task_id, order_id from task_id-order_id-morse.png."""
        result = handler._extract_task_info("/path/task1-order1-morse123.png")
        assert result is not None
        assert result["task_id"] == "task1"
        assert result["order_id"] == "order1"
        assert result["filename"] == "task1-order1-morse123.png"

    def test_valid_filename_with_image_suffix(self, handler):
        """Extracts task_id, order_id from task_id-order_id-morse_image_2.png."""
        result = handler._extract_task_info("/path/task1-order1-morse123_image_2.png")
        assert result is not None
        assert result["task_id"] == "task1"
        assert result["order_id"] == "order1"
        assert result["filename"] == "task1-order1-morse123_image_2.png"

    def test_invalid_too_few_parts(self, handler):
        """Returns None when filename has fewer than 2 parts after split."""
        result = handler._extract_task_info("/path/single.png")
        assert result is None

    def test_invalid_empty_name(self, handler):
        """Returns None when filename has no basename (edge case)."""
        result = handler._extract_task_info("/path/.png")
        assert result is None


class TestExtractUserInfo:
    """Test _extract_user_info."""

    @pytest.fixture
    def handler(self):
        base = os.path.normpath("/base/users")
        return HotFolderEventHandler(
            api_client=Mock(),
            network_paths=["/network"],
            user_paths=[base],
            completed_paths=[],
        )

    def test_valid_path_with_user_id_and_name(self, handler):
        """Extracts user_id and user_name from path under user_paths."""
        path = os.path.join("/base/users", "123-John Doe", "file.png")
        result = handler._extract_user_info(path)
        assert result is not None
        assert result["user_id"] == "123"
        assert result["user_name"] == "John Doe"

    def test_valid_path_user_folder_only(self, handler):
        """Extracts user when folder has no dash (user_id only)."""
        path = os.path.join("/base/users", "123", "file.png")
        result = handler._extract_user_info(path)
        assert result is not None
        assert result["user_id"] == "123"
        assert result["user_name"] == "123"

    def test_returns_none_when_not_under_user_paths(self, handler):
        """Returns None when path is not under user_paths."""
        result = handler._extract_user_info("/other/path/123-John/file.png")
        assert result is None

    def test_returns_none_when_insufficient_parts(self, handler):
        """Returns None when path has no subfolder under users base."""
        path = os.path.join("/base/users", "file.png")  # file directly in users
        result = handler._extract_user_info(path)
        assert result is None


class TestCompletedFolderCompletion:
    """Completion via move into completed folder."""

    def test_handle_moved_to_completed_calls_complete_task(self):
        api = Mock()
        api.complete_task.return_value = True
        handler = HotFolderEventHandler(
            api_client=api,
            network_paths=["/network"],
            user_paths=["/network/users"],
            completed_paths=["/network/completed"],
        )
        src = os.path.join("/network/users", "123-Jane Doe", "t1-order1-morse.png")
        dest = os.path.join("/network/completed", "t1-order1-morse.png")
        handler._moved_to_completed_sources[dest] = src
        handler._handle_moved_to_completed(dest)
        api.complete_task.assert_called_once_with("t1", "123")

    def test_handle_deleted_without_reassign_does_not_complete(self):
        api = Mock()
        handler = HotFolderEventHandler(
            api_client=api,
            network_paths=["/network"],
            user_paths=["/network/users"],
            completed_paths=["/network/completed"],
        )
        path = os.path.join("/network/users", "123-Jane Doe", "t1-order1-morse.png")
        handler._handle_deleted(path)
        api.complete_task.assert_not_called()
        api.unassign_task.assert_not_called()

    def test_on_moved_user_to_completed_queues_completion_not_moved_out(self):
        api = Mock()
        handler = HotFolderEventHandler(
            api_client=api,
            network_paths=["/network"],
            user_paths=["/network/users"],
            completed_paths=["/network/completed"],
        )

        class Ev:
            src_path = os.path.join("/network/users", "123-Name", "t1-order1-m.png")
            dest_path = os.path.join("/network/completed", "t1-order1-m.png")
            is_directory = False

        handler.on_moved(Ev())
        pending = handler._debouncer._pending_events
        assert any(k.startswith("moved_to_completed:") for k in pending)
        assert not any(k.startswith("moved_out:") for k in pending)
