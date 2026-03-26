"""OpenAlex API client for academic work metadata.

Base URL: https://api.openalex.org

Key endpoints:
    GET /works?search={query}         — Search works
    GET /works/{id}                   — Get a single work
    GET /works?filter=cites:{id}      — Get works citing a given work

Auth: None required. Add ?mailto=user@email.com for polite pool (100 req/s vs 10).

Rate limits:
    - Unauthenticated: 10 req/s
    - With mailto (polite pool): 100 req/s

Docs: https://docs.openalex.org
"""

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import openalex_limiter

logger = logging.getLogger("research-mcp-server")

OPENALEX_BASE_URL = "https://api.openalex.org"


def _reconstruct_abstract(inverted_index: Optional[dict[str, list[int]]]) -> str:
    """Reconstruct abstract from OpenAlex's inverted index format.

    OpenAlex stores abstracts as ``{"word": [pos1, pos2], ...}``.
    We rebuild the original text by sorting on position.

    Args:
        inverted_index: Mapping of word to list of positions, or None.

    Returns:
        Reconstructed abstract string, or empty string if unavailable.
    """
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indices in inverted_index.items():
        for idx in indices:
            positions.append((idx, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def _normalize_work(work: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAlex work to our standard paper format.

    Args:
        work: Raw work dict from the OpenAlex API.

    Returns:
        Normalized paper dict with consistent keys across sources.
    """
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
    primary_location = work.get("primary_location") or {}
    source_info = primary_location.get("source") or {}
    open_access = work.get("open_access") or {}

    return {
        "id": work.get("id", "").replace("https://openalex.org/", ""),
        "source": "openalex",
        "source_id": work.get("id"),
        "doi": work.get("doi"),
        "title": work.get("title", ""),
        "authors": [
            a["author"]["display_name"]
            for a in work.get("authorships", [])
            if a.get("author", {}).get("display_name")
        ],
        "abstract": abstract,
        "published_date": work.get("publication_date"),
        "citation_count": work.get("cited_by_count"),
        "categories": [
            c["display_name"] for c in work.get("concepts", [])[:5]
        ],
        "url": primary_location.get("landing_page_url") or work.get("doi"),
        "open_access": open_access.get("is_oa", False),
        "venue": source_info.get("display_name"),
    }


class OpenAlexClient:
    """Async client for the OpenAlex API."""

    def __init__(self) -> None:
        self._email = os.environ.get("OPENALEX_EMAIL", "").strip() or None

    def _base_params(self) -> dict[str, str]:
        """Build base query parameters (mailto for polite pool)."""
        params: dict[str, str] = {}
        if self._email:
            params["mailto"] = self._email
        return params

    async def _request(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Any:
        """Make a GET request with rate limiting and retry logic.

        Args:
            path: URL path appended to base URL.
            params: Query parameters.
            max_retries: Max retries on 429.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP errors.
            ValueError: If resource not found (404).
        """
        await openalex_limiter.wait()
        url = f"{OPENALEX_BASE_URL}{path}"

        merged_params = {**self._base_params(), **(params or {})}

        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url, params=merged_params
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 404:
                    raise ValueError(f"Resource not found on OpenAlex: {path}")

                if response.status_code == 429:
                    delay = 2 ** attempt
                    logger.warning(
                        f"OpenAlex rate limited (429), retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()

        raise httpx.HTTPStatusError(
            "Max retries exceeded on 429",
            request=httpx.Request("GET", url),
            response=response,  # type: ignore[possibly-undefined]
        )

    async def search_works(
        self,
        query: str,
        per_page: int = 25,
        page: int = 1,
        filters: Optional[dict[str, str]] = None,
    ) -> list[dict[str, Any]]:
        """Search OpenAlex works.

        Args:
            query: Free-text search query.
            per_page: Number of results per page (max 200).
            page: Page number (1-indexed).
            filters: Dict of OpenAlex filter key-value pairs, e.g.
                ``{"from_publication_date": "2024-01-01", "type": "article"}``.

        Returns:
            List of normalized paper dicts.
        """
        params: dict[str, Any] = {
            "search": query,
            "per_page": min(per_page, 200),
            "page": page,
        }

        if filters:
            filter_str = ",".join(f"{k}:{v}" for k, v in filters.items())
            params["filter"] = filter_str

        result = await self._request("/works", params=params)
        works = result.get("results", [])
        return [_normalize_work(w) for w in works]

    async def get_work(self, work_id: str) -> dict[str, Any]:
        """Get a single work by OpenAlex ID or DOI.

        Args:
            work_id: OpenAlex ID (e.g. ``W1234567890``) or full DOI URL.

        Returns:
            Normalized paper dict.
        """
        result = await self._request(f"/works/{work_id}")
        return _normalize_work(result)

    async def get_work_by_doi(self, doi: str) -> dict[str, Any]:
        """Convenience method to get a work by DOI.

        Args:
            doi: DOI string, e.g. ``10.1234/example``.

        Returns:
            Normalized paper dict.
        """
        # OpenAlex accepts DOIs via the /works endpoint with https://doi.org/ prefix
        doi_url = doi if doi.startswith("http") else f"https://doi.org/{doi}"
        return await self.get_work(doi_url)

    async def get_citations(
        self,
        work_id: str,
        per_page: int = 25,
    ) -> list[dict[str, Any]]:
        """Get works that cite a given work.

        Args:
            work_id: OpenAlex work ID (e.g. ``W1234567890``).
            per_page: Number of citing works to return.

        Returns:
            List of normalized paper dicts for citing works.
        """
        # Ensure we use the short ID form for the filter
        short_id = work_id.replace("https://openalex.org/", "")
        params: dict[str, Any] = {
            "filter": f"cites:{short_id}",
            "per_page": min(per_page, 200),
        }
        result = await self._request("/works", params=params)
        works = result.get("results", [])
        return [_normalize_work(w) for w in works]
