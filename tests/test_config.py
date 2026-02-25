"""Tests for the configuration module."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

from arxiv_mcp_server.config import Settings


@patch.object(Path, "mkdir")
def test_storage_path_default(mock_mkdir):
    """Test that the default storage path is correctly constructed."""
    expected_path = Path(__file__).resolve().parents[1] / "storage" / "papers"

    with patch.object(sys, "argv", ["program"]), patch.dict(os.environ, {}, clear=True):
        settings = Settings()
        assert settings.STORAGE_PATH == expected_path.resolve()

    # Verify mkdir was called with parents=True and exist_ok=True
    mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)


@patch.object(Path, "mkdir")
def test_storage_path_from_env(mock_mkdir):
    """Test that the storage path from env var is correctly parsed."""
    test_path = "/tmp/test_storage_env"

    with patch.object(sys, "argv", ["program"]), patch.dict(
        os.environ, {"ARXIV_STORAGE_PATH": test_path}, clear=True
    ):
        settings = Settings()
        assert settings.STORAGE_PATH == Path(test_path).resolve()

    mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)


@patch.object(Path, "mkdir")
def test_storage_path_from_args(mock_mkdir):
    """Test that the storage path from command line args is correctly parsed."""
    test_path = "/tmp/test_storage"

    with patch.object(sys, "argv", ["program", "--storage-path", test_path]), patch.dict(
        os.environ, {"ARXIV_STORAGE_PATH": "/tmp/ignored_env"}, clear=True
    ):
        settings = Settings()
        assert settings.STORAGE_PATH == Path(test_path).resolve()

    mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)


def test_storage_path_platform_compatibility():
    """Test that command-line path parsing works for different path formats."""
    test_paths = [
        # Unix-style path
        "/path/to/storage",
        # Windows-style path
        "C:\\path\\to\\storage",
        # Path with spaces
        "/path with spaces/to/storage",
        # Path with non-ASCII characters
        "/path/to/störâgè",
    ]

    for test_path in test_paths:
        with patch.object(sys, "argv", ["program", "--storage-path", test_path]):
            settings = Settings()
            parsed_path = settings._get_storage_path_from_args()

            # Verify that Path constructor handled the test path format
            assert parsed_path == Path(test_path).expanduser()


def test_storage_path_creates_missing_directory():
    """Test that directories are actually created for the storage path."""
    import tempfile

    # Create a temporary directory for our test
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a path that doesn't exist yet
        test_path = os.path.join(tmpdir, "deeply", "nested", "directory", "structure")

        # Make sure it doesn't exist yet
        assert not os.path.exists(test_path)

        # Patch the arguments to use this path
        with patch.object(sys, "argv", ["program", "--storage-path", test_path]):
            # Access the STORAGE_PATH property which should create the directories
            settings = Settings()
            storage_path = settings.STORAGE_PATH

            # Verify the directory was created
            assert os.path.exists(test_path)
            assert os.path.isdir(test_path)

            # Verify the paths refer to the same location
            # Use Path.samefile to handle symlinks (like /var -> /private/var on macOS)
            assert Path(storage_path).samefile(test_path)


def test_path_normalization_with_windows_paths():
    """Test Windows-specific path handling using string operations only."""
    # Windows-style paths - we'll test the normalization and joining logic
    windows_style_paths = [
        # Drive letter with backslashes
        "C:\\Users\\username\\Documents\\Papers",
        # UNC path (network share)
        "\\\\server\\share\\papers",
        # Drive letter with forward slashes (also valid on Windows)
        "C:/Users/username/Documents/Papers",
        # Windows-style path with spaces
        "C:\\Program Files\\arXiv\\papers",
        # Windows-style path with mixed slashes
        "C:\\Users/username\\Documents/Papers",
    ]

    # Test that our config works with these path formats
    for windows_path in windows_style_paths:
        assert Path(windows_path)  # This should not raise an error

        # Test path joining logic works correctly
        subpath = Path(windows_path) / "subdir"
        assert str(subpath).endswith("subdir")

        # The following check is problematic on real Windows systems
        # where the path separator may be different
        # Check only that the base path is contained in the result (ignoring separator differences)
        base_path_norm = windows_path.replace("\\", "/").replace("//", "/")
        subpath_norm = str(subpath).replace("\\", "/").replace("//", "/")
        assert base_path_norm in subpath_norm

        # Instead of checking exact string equality, verify the Path objects are equivalent
        assert subpath == Path(windows_path).joinpath("subdir")
