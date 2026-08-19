"""Tests for the configuration module."""

import os
import sys
from pathlib import Path
from types import ModuleType
from arxiv_mcp_server.config import Settings
from unittest.mock import MagicMock, patch


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


def test_close_arxiv_client_releases_shared_session(monkeypatch):
    from arxiv_mcp_server import config

    client = MagicMock()
    monkeypatch.setattr(config, "_arxiv_client", client)

    config.close_arxiv_client()

    client._session.close.assert_called_once_with()
    assert config._arxiv_client is None


def test_package_version_prefers_generated_bundle_version(monkeypatch):
    from arxiv_mcp_server import config

    bundle_module = ModuleType("arxiv_mcp_server._bundle_version")
    setattr(bundle_module, "VERSION", "9.9.9")
    monkeypatch.setitem(sys.modules, bundle_module.__name__, bundle_module)
    monkeypatch.setattr(config, "version", lambda _name: "0.6.2")

    assert config._resolve_package_version() == "9.9.9"


def test_package_version_uses_distribution_metadata_without_bundle(monkeypatch):
    from arxiv_mcp_server import config

    def missing_bundle(_name, *, package):
        raise ImportError(package)

    monkeypatch.setattr(config, "import_module", missing_bundle)
    monkeypatch.setattr(config, "version", lambda _name: "0.6.2")

    assert config._resolve_package_version() == "0.6.2"


def test_package_version_falls_back_without_bundle_or_distribution(monkeypatch):
    from arxiv_mcp_server import config

    def missing_bundle(_name, *, package):
        raise ImportError(package)

    def missing_distribution(_name):
        raise config.PackageNotFoundError

    monkeypatch.setattr(config, "import_module", missing_bundle)
    monkeypatch.setattr(config, "version", missing_distribution)

    assert config._resolve_package_version() == "0.0.0"


def test_get_arxiv_client_injects_timeout_and_disables_keepalive(monkeypatch):
    """Regression: a blackholed request must not hang forever.

    The upstream arxiv package sends requests with no timeout through a
    shared requests.Session; a silently-dropped (blackholed) connection
    then blocks inside ARXIV_RATE_LIMITER's lock forever, wedging every
    subsequent search until the server is restarted. The client must be
    built with HTTP timeouts and keep-alive disabled.
    """
    import sys
    from unittest.mock import MagicMock, patch
    import requests
    from arxiv_mcp_server import config

    real_session = requests.Session()
    fake_client = MagicMock()
    fake_client._session = real_session
    arxiv_mod = MagicMock()
    arxiv_mod.Client.return_value = fake_client

    monkeypatch.setattr(config, "_arxiv_client", None)
    monkeypatch.setitem(sys.modules, "arxiv", arxiv_mod)

    with patch.object(requests.Session, "get") as mock_get:
        client = config.get_arxiv_client()

        assert client is fake_client
        # session.get must be wrapped with a default timeout
        assert real_session.get.__name__ == "_get_with_timeout"

        # the wrapper must inject the default timeout on the underlying call
        real_session.get("https://export.arxiv.org/api/query")
        _args, kwargs = mock_get.call_args
        assert kwargs["timeout"] == (5.0, 30.0)

    # keep-alive must be disabled so no stale pooled connection is reused
    assert real_session.headers.get("Connection") == "close"


def test_get_arxiv_client_skips_wrapping_without_real_session(monkeypatch):
    """Mocks without a requests.Session must not be touched."""
    import sys
    from unittest.mock import MagicMock
    from arxiv_mcp_server import config

    fake_client = MagicMock()
    fake_client._session = MagicMock()  # not a requests.Session
    arxiv_mod = MagicMock()
    arxiv_mod.Client.return_value = fake_client

    monkeypatch.setattr(config, "_arxiv_client", None)
    monkeypatch.setitem(sys.modules, "arxiv", arxiv_mod)

    client = config.get_arxiv_client()

    assert client is fake_client
    # untouched: still a MagicMock, not the _get_with_timeout wrapper
    assert client._session.get.__class__.__name__ == "MagicMock"
