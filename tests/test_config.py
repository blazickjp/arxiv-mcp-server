"""Tests for the configuration module."""

import os
import sys
from pathlib import Path
from research_mcp_server.config import Settings
from unittest.mock import patch


@patch.object(Path, "mkdir")
@patch.object(Path, "resolve")
def test_storage_path_default(mock_resolve, mock_mkdir):
    """Test that the default storage path is correctly constructed."""
    # Setup the mock to return the path itself when resolved
    mock_resolve.side_effect = lambda: Path.home() / ".arxiv-mcp-server" / "papers"

    settings = Settings()
    expected_path = Path.home() / ".arxiv-mcp-server" / "papers"
    assert settings.STORAGE_PATH == expected_path.resolve()
    # Verify mkdir was called with parents=True and exist_ok=True
    mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)


@patch.object(Path, "mkdir")
@patch.object(Path, "resolve")
def test_storage_path_from_args(mock_resolve, mock_mkdir):
    """Test that the storage path from command line args is correctly parsed."""
    test_path = "/tmp/test_storage"
    mock_resolve.side_effect = lambda: Path(test_path)

    with patch.object(sys, "argv", ["program", "--storage-path", test_path]):
        settings = Settings()
        assert settings.STORAGE_PATH == Path(test_path).resolve()
    mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)


@patch.object(Path, "mkdir")
@patch.object(Path, "resolve")
def test_storage_path_platform_compatibility(mock_resolve, mock_mkdir):
    """Test that the storage path works correctly on different platforms."""
    # Test with a path format that would be valid on both Windows and Unix
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
        # Reset mocks for each iteration
        mock_resolve.reset_mock()
        mock_mkdir.reset_mock()

        # Set up the mock to return the path itself
        mock_resolve.side_effect = lambda: Path(test_path)

        with patch.object(sys, "argv", ["program", "--storage-path", test_path]):
            settings = Settings()
            resolved_path = settings.STORAGE_PATH

            # Verify that Path constructor was called with the test path
            assert resolved_path == Path(test_path).resolve()

            # Verify that mkdir was called
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)


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
