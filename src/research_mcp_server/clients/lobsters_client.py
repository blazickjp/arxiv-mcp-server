"""Lobsters API client.

Base URL: https://lobste.rs
No auth required. Rate limit: ~0.5 req/s (be polite).
"""

import logging
from typing import Any

import httpx

from ..utils.rate_limiter import lobsters_limiter

logger = logging.getLogger("research-mcp-server")

LOBSTERS_BASE = "https://lobste.rs"


def _normalize_story(story: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Lobsters story to standard format."""
    return {
        "id": story.get("short_id", ""),
        "source": "lobsters",
        "title": story.get("title", ""),
        "description": story.get("description", ""),
        "url": story.get("url", ""),
        "author": story.get("submitter_user", {}).get("username", "")
        if isinstance(story.get("submitter_user"), dict)
        else story.get("submitter_user", ""),
        "tags": story.get("tags", []),
        "score": story.get("score", 0),
        "comment_count": story.get("comment_count", 0),
        "created_at": story.get("created_at", ""),
        "lobsters_url": story.get(
            "comments_url", f"{LOBSTERS_BASE}/s/{story.get('short_id', '')}"
        ),
    }


class LobstersClient:
    """Async client for Lobsters."""

    async def _request(self, path: str) -> Any:
        await lobsters_limiter.wait()
        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{LOBSTERS_BASE}{path}", headers=headers
            )
            resp.raise_for_status()
            return resp.json()

    async def hottest(self, max_results: int = 20) -> list[dict[str, Any]]:
        """Get hottest stories."""
        stories = await self._request("/hottest.json")
        return [_normalize_story(s) for s in stories[:max_results]]

    async def newest(self, max_results: int = 20) -> list[dict[str, Any]]:
        """Get newest stories."""
        stories = await self._request("/newest.json")
        return [_normalize_story(s) for s in stories[:max_results]]

    async def by_tag(
        self, tag: str, max_results: int = 20
    ) -> list[dict[str, Any]]:
        """Get stories by tag (e.g., 'python', 'ai', 'rust')."""
        stories = await self._request(f"/t/{tag}.json")
        return [_normalize_story(s) for s in stories[:max_results]]
