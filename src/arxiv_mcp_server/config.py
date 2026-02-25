"""Configuration settings for the arXiv MCP server."""

import logging
import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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

        Precedence:
        1. --storage-path command line argument
        2. ARXIV_STORAGE_PATH environment variable
        3. Repository default: <repo>/storage/papers

        Returns:
            Path: The absolute storage path.
        """
        path = (
            self._get_storage_path_from_args()
            or self._get_storage_path_from_env()
            or self._get_default_storage_path()
        )
        path = path.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _get_default_storage_path(self) -> Path:
        """Return default storage path in the project folder."""
        return Path(__file__).resolve().parents[2] / "storage" / "papers"

    def _get_storage_path_from_env(self) -> Path | None:
        """Extract storage path from ARXIV_STORAGE_PATH if provided."""
        raw_path = os.getenv("ARXIV_STORAGE_PATH")
        if not raw_path:
            return None

        try:
            return Path(raw_path).expanduser()
        except (TypeError, ValueError) as e:
            logger.warning(f"Invalid ARXIV_STORAGE_PATH format: {e}")
        except OSError as e:
            logger.warning(f"Invalid ARXIV_STORAGE_PATH value: {e}")

        return None

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

        # Try to parse the path
        try:
            path = Path(args[storage_path_index + 1]).expanduser()
            return path
        except (TypeError, ValueError) as e:
            # TypeError: If the path argument is not string-like
            # ValueError: If the path string is malformed
            logger.warning(f"Invalid storage path format: {e}")
        except OSError as e:
            # OSError: If the path contains invalid characters or is too long
            logger.warning(f"Invalid storage path: {e}")

        return None
