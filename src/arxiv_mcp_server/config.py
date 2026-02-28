"""Configuration settings for the arXiv MCP server."""

import sys
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Lazy shared arxiv client
_arxiv_client = None


def get_arxiv_client():
    """Return a shared arxiv.Client instance, creating it on first call."""
    global _arxiv_client
    if _arxiv_client is None:
        import arxiv

        _arxiv_client = arxiv.Client()
    return _arxiv_client


class Settings(BaseSettings):
    """Server configuration settings."""

    APP_NAME: str = "arxiv-mcp-server"
    APP_VERSION: str = "0.3.2"
    MAX_RESULTS: int = 50
    BATCH_SIZE: int = 20
    REQUEST_TIMEOUT: int = 60
    HOST: str = "0.0.0.0"
    PORT: int = 8000
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
