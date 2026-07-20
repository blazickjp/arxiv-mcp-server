"""Resource management for the arXiv MCP server."""

from typing import Any

__all__ = ["PaperManager"]


def __getattr__(name: str) -> Any:
    """Preserve the legacy export without importing PDF dependencies eagerly."""
    if name == "PaperManager":
        from .papers import PaperManager

        return PaperManager
    raise AttributeError(name)
