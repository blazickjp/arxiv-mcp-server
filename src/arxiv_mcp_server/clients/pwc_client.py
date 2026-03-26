"""Papers With Code API client.

Base URL: https://paperswithcode.com/api/v1
Auth: None required
Rate limit: ~5 req/s (be polite)

Key endpoints:
    GET /papers/          -- search papers
    GET /papers/{id}/     -- paper details
    GET /papers/{id}/repositories/  -- code repos
    GET /papers/{id}/results/       -- benchmark results (SOTA tables)
    GET /papers/{id}/methods/       -- methods used
    GET /papers/{id}/datasets/      -- datasets used
    GET /search/?q=query            -- search across everything

Paper IDs are URL slugs (e.g., "attention-is-all-you-need").
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("arxiv-mcp-server")

PWC_BASE_URL = "https://paperswithcode.com/api/v1"

pwc_limiter = RateLimiter(calls_per_second=5)


def _normalize_paper(paper: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Papers With Code paper response to a standard format.

    Args:
        paper: Raw paper dict from the PWC API.

    Returns:
        Normalized paper dict with consistent keys.
    """
    return {
        "id": paper.get("id", ""),
        "source": "papers_with_code",
        "source_id": paper.get("url_abs") or paper.get("id"),
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "abstract": paper.get("abstract", ""),
        "published_date": paper.get("published"),
        "arxiv_id": paper.get("arxiv_id"),
        "url": paper.get("url_abs"),
        "url_pdf": paper.get("url_pdf"),
        "proceeding": paper.get("proceeding"),
    }


class PapersWithCodeClient:
    """Async client for the Papers With Code API."""

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
            ValueError: If resource not found (404).
        """
        await pwc_limiter.wait()
        url = f"{PWC_BASE_URL}{path}"

        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers={"Accept": "application/json"},
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 404:
                    raise ValueError(
                        f"Resource not found on Papers With Code: {path}"
                    )

                if response.status_code == 429:
                    delay = 2 ** attempt
                    logger.warning(
                        f"PWC rate limited (429), retrying in {delay}s "
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

    async def search(
        self,
        query: str,
        items_per_page: int = 25,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """Search for papers on Papers With Code.

        Args:
            query: Search query string.
            items_per_page: Number of results per page (max 50).
            page: Page number (1-indexed).

        Returns:
            List of normalized paper dicts.
        """
        params = {
            "q": query,
            "items_per_page": min(items_per_page, 50),
            "page": page,
        }
        result = await self._request("GET", "/search/", params=params)
        papers = result.get("results", [])
        return [_normalize_paper(p) for p in papers]

    async def get_paper(self, paper_id: str) -> dict[str, Any]:
        """Get paper details by Papers With Code paper ID (URL slug).

        Args:
            paper_id: PWC paper slug (e.g., "attention-is-all-you-need").

        Returns:
            Normalized paper dict.
        """
        result = await self._request("GET", f"/papers/{paper_id}/")
        return _normalize_paper(result)

    async def get_repositories(self, paper_id: str) -> list[dict[str, Any]]:
        """Get GitHub repositories associated with a paper.

        Args:
            paper_id: PWC paper slug.

        Returns:
            List of repository dicts with url, stars, framework.
        """
        result = await self._request("GET", f"/papers/{paper_id}/repositories/")
        repos = result.get("results", []) if isinstance(result, dict) else result
        return [
            {
                "url": repo.get("url", ""),
                "stars": repo.get("stars", 0),
                "framework": repo.get("framework", ""),
                "is_official": repo.get("is_official", False),
                "description": repo.get("description", ""),
            }
            for repo in repos
        ]

    async def get_results(self, paper_id: str) -> list[dict[str, Any]]:
        """Get benchmark results (SOTA tables) for a paper.

        Args:
            paper_id: PWC paper slug.

        Returns:
            List of benchmark result dicts.
        """
        result = await self._request("GET", f"/papers/{paper_id}/results/")
        results_list = result.get("results", []) if isinstance(result, dict) else result
        return [
            {
                "task": r.get("task", ""),
                "dataset": r.get("dataset", ""),
                "metric": r.get("metric", ""),
                "value": r.get("value"),
                "rank": r.get("rank"),
                "methodology": r.get("methodology", ""),
            }
            for r in results_list
        ]

    async def get_methods(self, paper_id: str) -> list[dict[str, Any]]:
        """Get methods used in a paper.

        Args:
            paper_id: PWC paper slug.

        Returns:
            List of method dicts.
        """
        result = await self._request("GET", f"/papers/{paper_id}/methods/")
        methods = result.get("results", []) if isinstance(result, dict) else result
        return [
            {
                "name": m.get("name", ""),
                "full_name": m.get("full_name", ""),
                "description": m.get("description", ""),
                "url": m.get("url", ""),
            }
            for m in methods
        ]

    async def get_datasets(self, paper_id: str) -> list[dict[str, Any]]:
        """Get datasets used in a paper.

        Args:
            paper_id: PWC paper slug.

        Returns:
            List of dataset dicts.
        """
        result = await self._request("GET", f"/papers/{paper_id}/datasets/")
        datasets = result.get("results", []) if isinstance(result, dict) else result
        return [
            {
                "name": d.get("name", ""),
                "full_name": d.get("full_name", ""),
                "url": d.get("url", ""),
                "description": d.get("description", ""),
                "num_papers": d.get("num_papers", 0),
            }
            for d in datasets
        ]
