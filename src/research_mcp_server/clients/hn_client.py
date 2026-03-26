"""Hacker News API client (Algolia search + Firebase).

Algolia Search: https://hn.algolia.com/api/v1
Firebase: https://hacker-news.firebaseio.com/v0

No auth required. Rate limit: ~10 req/s (be polite).
"""

import logging
from typing import Any, Optional
from datetime import datetime, timedelta

import httpx

from ..utils.rate_limiter import hn_limiter

logger = logging.getLogger("research-mcp-server")

ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
FIREBASE_BASE = "https://hacker-news.firebaseio.com/v0"


def _normalize_story(hit: dict[str, Any]) -> dict[str, Any]:
    """Normalize an Algolia HN hit to standard format."""
    return {
        "id": hit.get("objectID", ""),
        "source": "hackernews",
        "title": hit.get("title", ""),
        "url": hit.get("url", ""),
        "author": hit.get("author", ""),
        "points": hit.get("points", 0),
        "num_comments": hit.get("num_comments", 0),
        "created_at": hit.get("created_at", ""),
        "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
        "story_text": hit.get("story_text", ""),
    }


def _normalize_comment(hit: dict[str, Any]) -> dict[str, Any]:
    """Normalize an Algolia HN comment."""
    return {
        "id": hit.get("objectID", ""),
        "author": hit.get("author", ""),
        "text": hit.get("comment_text", ""),
        "points": hit.get("points"),
        "created_at": hit.get("created_at", ""),
        "parent_id": hit.get("parent_id"),
        "story_id": hit.get("story_id"),
    }


class HNClient:
    """Async client for Hacker News APIs."""

    async def _request(self, base: str, path: str, params: dict | None = None) -> Any:
        await hn_limiter.wait()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base}{path}", params=params or {})
            resp.raise_for_status()
            return resp.json()

    async def search(
        self,
        query: str,
        search_type: str = "story",  # story or comment
        sort: str = "relevance",  # relevance or date
        time_range: str | None = None,  # 24h, week, month, year
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search HN stories or comments via Algolia."""
        endpoint = "/search" if sort == "relevance" else "/search_by_date"
        params: dict[str, Any] = {
            "query": query,
            "tags": search_type,
            "hitsPerPage": min(max_results, 50),
        }

        if time_range:
            now = int(datetime.now().timestamp())
            ranges = {"24h": 86400, "week": 604800, "month": 2592000, "year": 31536000}
            seconds = ranges.get(time_range, 604800)
            params["numericFilters"] = f"created_at_i>{now - seconds}"

        data = await self._request(ALGOLIA_BASE, endpoint, params)
        hits = data.get("hits", [])

        if search_type == "comment":
            return [_normalize_comment(h) for h in hits]
        return [_normalize_story(h) for h in hits]

    async def trending(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get current front page stories via Firebase."""
        story_ids = await self._request(FIREBASE_BASE, "/topstories.json")
        stories = []
        for sid in story_ids[:min(limit, 30)]:
            try:
                item = await self._request(FIREBASE_BASE, f"/item/{sid}.json")
                if item and item.get("type") == "story":
                    stories.append({
                        "id": str(item.get("id", "")),
                        "source": "hackernews",
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "author": item.get("by", ""),
                        "points": item.get("score", 0),
                        "num_comments": item.get("descendants", 0),
                        "created_at": datetime.fromtimestamp(item.get("time", 0)).isoformat(),
                        "hn_url": f"https://news.ycombinator.com/item?id={item.get('id', '')}",
                    })
            except Exception as e:
                logger.debug(f"Failed to fetch HN story {sid}: {e}")
                continue
        return stories

    async def get_discussion(self, story_id: str, max_comments: int = 20) -> dict[str, Any]:
        """Get a story's top comments via Algolia."""
        # Get story details
        story_data = await self._request(ALGOLIA_BASE, f"/items/{story_id}")

        # Get top comments (sorted by points via search)
        comments_data = await self._request(ALGOLIA_BASE, "/search", {
            "tags": f"comment,story_{story_id}",
            "hitsPerPage": min(max_comments, 50),
        })

        comments = [_normalize_comment(h) for h in comments_data.get("hits", [])]

        return {
            "story": {
                "id": story_id,
                "title": story_data.get("title", ""),
                "url": story_data.get("url", ""),
                "author": story_data.get("author", ""),
                "points": story_data.get("points", 0),
                "num_comments": story_data.get("children", []),
            },
            "top_comments": comments,
        }
