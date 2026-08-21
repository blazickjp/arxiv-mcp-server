"""Configuration settings for the arXiv MCP server."""

import sys
from importlib import import_module
from importlib.metadata import version, PackageNotFoundError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import logging
import requests


def _resolve_package_version() -> str:
    """Resolve the bundled version first, then installed package metadata."""
    try:
        bundle_version = import_module("._bundle_version", package=__package__)
    except ImportError:
        try:
            return version("arxiv-mcp-server")
        except PackageNotFoundError:
            return "0.0.0"
    return bundle_version.VERSION


_PACKAGE_VERSION = _resolve_package_version()

logger = logging.getLogger(__name__)

# Lazy shared arxiv client — created on first use, not at import time
_arxiv_client = None


def get_arxiv_client():
    """Return the process-wide arxiv.Client, creating it on first use.

    Callers that need a particular page size must set it while holding the
    shared arXiv request gate. This preserves one requests.Session without
    allowing concurrent searches to race over client configuration.
    """
    global _arxiv_client
    if _arxiv_client is None:
        import arxiv

        client = arxiv.Client()

        # The upstream arxiv package issues HTTP requests through a
        # requests.Session with no timeout (arxiv.Client._session.get),
        # so a connection that silently stops responding (a "black hole":
        # the peer never answers again, no FIN/RST — root cause unknown,
        # see issue) blocks forever inside ARXIV_RATE_LIMITER's
        # process-wide lock, wedging every subsequent search until the
        # server is restarted.
        #
        # 1. Inject connect/read timeouts so such a request fails within
        #    ~35s, the lock is released, and later calls recover.
        # 2. Disable keep-alive connection reuse so a pooled connection
        #    can never be reused after going stale (urllib3's stale check
        #    only verifies the socket object exists, not that the peer is
        #    still reachable).
        #
        # Only patch a real requests.Session; tests may substitute a mock
        # client without one.
        session = getattr(client, "_session", None)
        if isinstance(session, requests.Session):
            _orig_get = session.get

            def _get_with_timeout(url, **kwargs):
                kwargs.setdefault("timeout", (5.0, 30.0))
                return _orig_get(url, **kwargs)

            session.get = _get_with_timeout
            # requests' default headers already include 'Connection: keep-alive',
            # so setdefault would be a no-op; assign directly.
            session.headers["Connection"] = "close"

        _arxiv_client = client
    return _arxiv_client


def close_arxiv_client() -> None:
    """Close the shared HTTP session and clear the process-wide client."""
    global _arxiv_client
    if _arxiv_client is None:
        return
    session = getattr(_arxiv_client, "_session", None)
    close = getattr(session, "close", None)
    if callable(close):
        close()
    _arxiv_client = None


class Settings(BaseSettings):
    """Server configuration settings."""

    APP_NAME: str = "arxiv-mcp-server"
    APP_VERSION: str = _PACKAGE_VERSION
    MAX_RESULTS: int = 50
    BATCH_SIZE: int = 20
    REQUEST_TIMEOUT: int = 60
    TRANSPORT: str = "stdio"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    ALLOWED_HOSTS: str = ""
    ALLOWED_ORIGINS: str = ""
    SEMANTIC_SCHOLAR_API_KEY: str = ""
    model_config = SettingsConfigDict(extra="allow")

    @property
    def STORAGE_PATH(self) -> Path:
        """Get the resolved storage path and ensure it exists.

        Returns:
            Path: The absolute storage path.
        """
        path = (
            self._get_storage_path_from_args()
            or Path.home() / ".arxiv-mcp-server" / "papers"
        )
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _get_storage_path_from_args(self) -> Path | None:
        """Extract storage path from command line arguments.

        Returns:
            Path | None: The storage path if specified in arguments, None otherwise.
        """
        args = sys.argv[1:]

        # If not enough arguments
        if len(args) < 2:
            return None

        # Look for the --storage-path option
        try:
            storage_path_index = args.index("--storage-path")
        except ValueError:
            return None

        # Early return if --storage-path is the last argument
        if storage_path_index + 1 >= len(args):
            return None

        # Try to resolve the path
        try:
            path = Path(args[storage_path_index + 1])
            return path.resolve()
        except (TypeError, ValueError) as e:
            # TypeError: If the path argument is not string-like
            # ValueError: If the path string is malformed
            logger.warning(f"Invalid storage path format: {e}")
        except OSError as e:
            # OSError: If the path contains invalid characters or is too long
            logger.warning(f"Invalid storage path: {e}")

        return None
