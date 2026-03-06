"""Tests for utility functions."""

import pytest
from src.utils import generate_filename, ensure_unique_filename


class TestGenerateFilename:
    """Test filename generation."""

    def test_single_image_with_task_id(self):
        result = generate_filename("ORD123", "AG456", ["A", "B"], "morse123", "ses_abc", 0)
        assert result == "ses_abc-ORD123-morse123"

    def test_multiple_images(self):
        result = generate_filename("ORD123", "AG456", ["A", "B"], "morse123", "ses_abc", 1)
        assert result == "ses_abc-ORD123-morse123_image_1"

        result = generate_filename("ORD123", "AG456", ["A", "B"], "morse123", "ses_abc", 2)
        assert result == "ses_abc-ORD123-morse123_image_2"

    def test_no_morse_code(self):
        result = generate_filename("ORD123", "AG456", [], None, "ses_abc", 0)
        assert result == "ses_abc-ORD123"


class TestEnsureUniqueFilename:
    """Test unique filename generation."""

    def test_unique_path(self, tmp_path):
        directory = str(tmp_path)
        result = ensure_unique_filename(directory, "test", ".txt")
        assert result == str(tmp_path / "test.txt")

    def test_duplicate_filename(self, tmp_path):
        directory = str(tmp_path)
        # Create existing file
        (tmp_path / "test.txt").touch()
        result = ensure_unique_filename(directory, "test", ".txt")
        assert result == str(tmp_path / "test-1.txt")

    def test_multiple_duplicates(self, tmp_path):
        directory = str(tmp_path)
        # Create existing files
        (tmp_path / "test.txt").touch()
        (tmp_path / "test-1.txt").touch()
        result = ensure_unique_filename(directory, "test", ".txt")
        assert result == str(tmp_path / "test-2.txt")
