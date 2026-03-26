"""Hugging Face Hub API client for papers, models, and datasets.

Base URL: https://huggingface.co/api

Key endpoints:
    GET /daily_papers              -- Trending papers (optionally by date)
    GET /papers?search=query       -- Search papers by keyword
    GET /papers/{arxiv_id}         -- Paper detail with linked models/datasets/spaces
    GET /models?search=query       -- Search models
    GET /datasets?search=query     -- Search datasets

Auth: Optional HF_TOKEN for higher rate limits.
Rate limit: ~10 req/s (be polite).
"""

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import hf_limiter

logger = logging.getLogger("research-mcp-server")

HF_BASE_URL = "https://huggingface.co/api"


def _normalize_paper(paper: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Hugging Face paper response to a standard format.

    Args:
        paper: Raw paper dict from HF API (may be nested under "paper" key).

    Returns:
        Normalized paper dict with consistent fields.
    """
    p = paper.get("paper", paper)
    return {
        "id": p.get("id", ""),
        "source": "huggingface",
        "source_id": p.get("id"),
        "title": p.get("title", ""),
        "authors": [a.get("name", a.get("user", "")) for a in p.get("authors", [])],
        "abstract": p.get("summary", ""),
        "published_date": p.get("publishedAt"),
        "arxiv_id": p.get("id"),  # HF paper IDs are arxiv IDs
        "upvotes": paper.get("numUpvotes", paper.get("upvotes", 0)),
        "url": f"https://huggingface.co/papers/{p.get('id', '')}",
    }


class HuggingFaceClient:
    """Async client for the Hugging Face Hub API."""

    def __init__(self) -> None:
        token = os.environ.get("HF_TOKEN", "").strip()
        self._token: Optional[str] = token if token else None

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Any:
        """Make an API request with rate limiting and retry logic.

        Args:
            method: HTTP method.
            path: URL path (appended to base URL).
            params: Query parameters.
            max_retries: Max retries on 429.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP errors.
            ValueError: On 404 responses.
        """
        await hf_limiter.wait()
        url = f"{HF_BASE_URL}{path}"

        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method, url, params=params, headers=self._headers()
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 404:
                    raise ValueError(f"Not found: {path}")

                if response.status_code == 429:
                    delay = 2**attempt
                    logger.warning(
                        f"HF rate limited (429), retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()

        raise httpx.HTTPStatusError(
            "Max retries exceeded on 429",
            request=httpx.Request(method, url),
            response=response,  # type: ignore[possibly-undefined]
        )

    async def get_daily_papers(
        self, date: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Get trending papers for a given date.

        Args:
            date: Date string in YYYY-MM-DD format. If None, returns today's papers.

        Returns:
            List of normalized paper dicts with upvotes, title, arxiv_id, etc.
        """
        params: dict[str, Any] = {}
        if date:
            params["date"] = date

        result = await self._request("GET", "/daily_papers", params=params or None)
        return [_normalize_paper(p) for p in result]

    async def search_papers(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Search papers by keyword.

        Args:
            query: Search query string.
            limit: Maximum number of results to return.

        Returns:
            List of normalized paper dicts.
        """
        params: dict[str, Any] = {"search": query}
        result = await self._request("GET", "/papers", params=params)
        papers = [_normalize_paper(p) for p in result]
        return papers[:limit]

    async def get_paper(self, arxiv_id: str) -> dict[str, Any]:
        """Get paper details including linked models, datasets, and spaces.

        Args:
            arxiv_id: arXiv paper ID (e.g., "2401.12345").

        Returns:
            Paper details dict.
        """
        return await self._request("GET", f"/papers/{arxiv_id}")

    async def search_models(
        self, query: str, limit: int = 10, sort: str = "likes"
    ) -> list[dict[str, Any]]:
        """Find HF models related to a query.

        Args:
            query: Search query string.
            limit: Maximum number of results to return.
            sort: Sort order (e.g., "likes", "downloads", "created").

        Returns:
            List of model dicts with id, likes, downloads, etc.
        """
        params: dict[str, Any] = {
            "search": query,
            "sort": sort,
            "limit": limit,
        }
        return await self._request("GET", "/models", params=params)

    async def search_datasets(
        self, query: str, limit: int = 10, sort: str = "likes"
    ) -> list[dict[str, Any]]:
        """Find HF datasets related to a query.

        Args:
            query: Search query string.
            limit: Maximum number of results to return.
            sort: Sort order (e.g., "likes", "downloads", "created").

        Returns:
            List of dataset dicts with id, likes, downloads, etc.
        """
        params: dict[str, Any] = {
            "search": query,
            "sort": sort,
            "limit": limit,
        }
        return await self._request("GET", "/datasets", params=params)
