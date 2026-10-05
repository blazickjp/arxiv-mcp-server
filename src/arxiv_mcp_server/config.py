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


def get_arxiv_client(num_retries=None):
    """Return an arxiv.Client with appropriate timeouts and connection settings.

    Args:
        num_retries: Override the number of retries. If None, uses the shared
                     process-wide client with default retries. If an integer,
                     creates a new client with that retry count (use 0 for
                     minimal retries to avoid prolonging 406 IP blocks).

    Callers that need a particular page size must set it while holding the
    shared arXiv request gate. This preserves one requests.Session without
    allowing concurrent searches to race over client configuration.
    """
    global _arxiv_client

    # If num_retries is specified, create a new client with that setting
    if num_retries is not None:
        import arxiv

        client = arxiv.Client(num_retries=num_retries, delay_seconds=3.0)
    # Otherwise use the shared client
    elif _arxiv_client is None:
        import arxiv

        client = arxiv.Client()
        _arxiv_client = client
    else:
        return _arxiv_client

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
        settings = Settings()

        def _get_with_timeout(url, **kwargs):
            kwargs.setdefault(
                "timeout",
                (
                    float(settings.ARXIV_CONNECT_TIMEOUT),
                    float(settings.get_request_timeout()),
                ),
            )
            return _orig_get(url, **kwargs)

        session.get = _get_with_timeout
        # requests' default headers already include 'Connection: keep-alive',
        # so setdefault would be a no-op; assign directly.
        session.headers["Connection"] = "close"

    return client


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
    REQUEST_TIMEOUT: int = 60  # Deprecated: use ARXIV_REQUEST_TIMEOUT
    ARXIV_REQUEST_TIMEOUT: int = 30
    ARXIV_CONNECT_TIMEOUT: int = 10
    ARXIV_MAX_RETRIES: int = 2
    ARXIV_HTTP_406_MAX_RETRIES: int = 1
    ARXIV_INITIAL_BACKOFF: float = 2.0
    ARXIV_MAX_BACKOFF: float = 30.0
    ARXIV_MAX_TOTAL_TIME: int = 50
    TRANSPORT: str = "stdio"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    ALLOWED_HOSTS: str = ""
    ALLOWED_ORIGINS: str = ""
    SEMANTIC_SCHOLAR_API_KEY: str = ""
    model_config = SettingsConfigDict(extra="allow")

    def model_post_init(self, __context) -> None:
        """Validate timeout settings after initialization."""
        # Only log validation warnings once per process
        if not hasattr(Settings, "_validation_logged"):
            Settings._validation_logged = False

        if Settings._validation_logged:
            # Still validate but don't log again
            if self.ARXIV_REQUEST_TIMEOUT <= 0:
                self.ARXIV_REQUEST_TIMEOUT = 30
            if self.ARXIV_CONNECT_TIMEOUT <= 0:
                self.ARXIV_CONNECT_TIMEOUT = 10
            if self.ARXIV_MAX_TOTAL_TIME <= 0:
                self.ARXIV_MAX_TOTAL_TIME = 50
            if self.ARXIV_MAX_BACKOFF <= 0:
                self.ARXIV_MAX_BACKOFF = 30.0
            return

        Settings._validation_logged = True

        # Validate positive timeouts
        if self.ARXIV_REQUEST_TIMEOUT <= 0:
            logger.warning(
                f"ARXIV_REQUEST_TIMEOUT must be positive (got {self.ARXIV_REQUEST_TIMEOUT}), using default 30"
            )
            self.ARXIV_REQUEST_TIMEOUT = 30
        if self.ARXIV_CONNECT_TIMEOUT <= 0:
            logger.warning(
                f"ARXIV_CONNECT_TIMEOUT must be positive (got {self.ARXIV_CONNECT_TIMEOUT}), using default 10"
            )
            self.ARXIV_CONNECT_TIMEOUT = 10
        if self.ARXIV_MAX_TOTAL_TIME <= 0:
            logger.warning(
                f"ARXIV_MAX_TOTAL_TIME must be positive (got {self.ARXIV_MAX_TOTAL_TIME}), using default 50"
            )
            self.ARXIV_MAX_TOTAL_TIME = 50
        if self.ARXIV_MAX_BACKOFF <= 0:
            logger.warning(
                f"ARXIV_MAX_BACKOFF must be positive (got {self.ARXIV_MAX_BACKOFF}), using default 30"
            )
            self.ARXIV_MAX_BACKOFF = 30.0

    def get_request_timeout(self) -> int:
        """Get request timeout with fallback to legacy REQUEST_TIMEOUT.

        Returns ARXIV_REQUEST_TIMEOUT if explicitly set (case-insensitive),
        otherwise falls back to REQUEST_TIMEOUT (case-insensitive) for
        backward compatibility. Non-positive values clamp to default 30.
        """
        # Check if ARXIV_REQUEST_TIMEOUT was explicitly set (case-insensitive)
        import os

        env_keys = {k.upper(): k for k in os.environ.keys()}

        if "ARXIV_REQUEST_TIMEOUT" in env_keys:
            return self.ARXIV_REQUEST_TIMEOUT
        # Fall back to REQUEST_TIMEOUT if it was explicitly set (case-insensitive)
        if "REQUEST_TIMEOUT" in env_keys:
            # Validate legacy value the same way as ARXIV_REQUEST_TIMEOUT
            if self.REQUEST_TIMEOUT <= 0:
                logger.warning(
                    f"REQUEST_TIMEOUT must be positive (got {self.REQUEST_TIMEOUT}), using default 30"
                )
                return 30
            return self.REQUEST_TIMEOUT
        # Use ARXIV_REQUEST_TIMEOUT default
        return self.ARXIV_REQUEST_TIMEOUT

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
