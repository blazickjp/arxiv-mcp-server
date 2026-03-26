"""Dev.to (Forem) API client.

Base URL: https://dev.to/api
No auth required for reading. Rate limit: ~10 req/s.
"""

import logging
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import devto_limiter

logger = logging.getLogger("research-mcp-server")

DEVTO_BASE = "https://dev.to/api"


def _normalize_article(article: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Dev.to article to standard format."""
    return {
        "id": str(article.get("id", "")),
        "source": "devto",
        "title": article.get("title", ""),
        "description": article.get("description", ""),
        "url": article.get("url", ""),
        "author": article.get("user", {}).get("username", ""),
        "author_name": article.get("user", {}).get("name", ""),
        "tags": article.get("tag_list", []),
        "published_at": article.get("published_at", ""),
        "positive_reactions_count": article.get("positive_reactions_count", 0),
        "comments_count": article.get("comments_count", 0),
        "reading_time_minutes": article.get("reading_time_minutes", 0),
    }


class DevtoClient:
    """Async client for the Dev.to API."""

    async def _request(self, path: str, params: dict | None = None) -> Any:
        await devto_limiter.wait()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{DEVTO_BASE}{path}", params=params or {})
            resp.raise_for_status()
            return resp.json()

    async def search(
        self,
        query: str,
        tag: str | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search Dev.to articles."""
        # Dev.to doesn't have a great search API, use /articles with tag or query
        params: dict[str, Any] = {"per_page": min(max_results, 30)}
        if tag:
            params["tag"] = tag
        # Use the search endpoint (undocumented but works)
        params["page"] = 1

        # Try Forem search first
        try:
            articles = await self._request("/articles", params)
        except Exception:
            articles = []

        # Filter by query if provided (Dev.to's filtering is limited)
        if query:
            query_lower = query.lower()
            filtered = [
                a
                for a in articles
                if query_lower
                in (a.get("title", "") + " " + a.get("description", "")).lower()
            ]
            # If filtering removes too many, return all
            articles = filtered if len(filtered) >= 3 else articles

        return [_normalize_article(a) for a in articles[:max_results]]

    async def trending(
        self, time_range: str = "week", max_results: int = 20
    ) -> list[dict[str, Any]]:
        """Get trending Dev.to articles."""
        # top=7 means top of last 7 days
        top_map = {"day": 1, "week": 7, "month": 30, "year": 365}
        top_val = top_map.get(time_range, 7)

        articles = await self._request(
            "/articles",
            {
                "top": top_val,
                "per_page": min(max_results, 30),
            },
        )
        return [_normalize_article(a) for a in articles[:max_results]]

    async def get_article(self, article_id: str) -> dict[str, Any]:
        """Get a full Dev.to article by ID."""
        article = await self._request(f"/articles/{article_id}")
        result = _normalize_article(article)
        result["body_markdown"] = article.get("body_markdown", "")
        return result
