"""Crossref API client for DOI resolution and reference metadata.

Base URL: https://api.crossref.org

Key endpoints:
    GET /works/{doi}                  — Resolve a DOI to metadata
    GET /works?query={query}          — Search works
    GET /works/{doi}                  — References embedded in the work record

Auth: None required. Add mailto header for polite pool (~50 req/s).

Rate limits:
    - Without mailto: best-effort, lower priority
    - With mailto header: ~50 req/s polite pool
"""

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import crossref_limiter

logger = logging.getLogger("arxiv-mcp-server")

CROSSREF_BASE_URL = "https://api.crossref.org"


def _extract_date(item: dict[str, Any]) -> Optional[str]:
    """Extract the best available publication date from a Crossref work.

    Crossref stores dates as ``{"date-parts": [[2024, 1, 15]]}``.
    We try ``published-print``, then ``published-online``, then ``issued``.

    Args:
        item: Raw Crossref work dict.

    Returns:
        ISO date string (YYYY-MM-DD) or None.
    """
    for field in ("published-print", "published-online", "issued"):
        date_obj = item.get(field)
        if not date_obj:
            continue
        parts = date_obj.get("date-parts", [[]])[0]
        if not parts:
            continue
        # parts = [year] or [year, month] or [year, month, day]
        year = parts[0]
        month = parts[1] if len(parts) > 1 else 1
        day = parts[2] if len(parts) > 2 else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _normalize_work(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a Crossref work item to our standard paper format.

    Args:
        item: Raw Crossref work dict from the ``message`` field.

    Returns:
        Normalized paper dict with consistent keys across sources.
    """
    authors = [
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in item.get("author", [])
    ]
    title_list = item.get("title", [])
    container_list = item.get("container-title", [])

    return {
        "id": item.get("DOI", ""),
        "source": "crossref",
        "source_id": item.get("DOI"),
        "doi": item.get("DOI"),
        "title": title_list[0] if title_list else "",
        "authors": authors,
        "abstract": item.get("abstract", ""),  # often missing in Crossref
        "published_date": _extract_date(item),
        "citation_count": item.get("is-referenced-by-count"),
        "categories": [],
        "url": item.get("URL"),
        "open_access": False,  # Crossref doesn't reliably provide OA status
        "venue": container_list[0] if container_list else None,
    }


class CrossrefClient:
    """Async client for the Crossref API."""

    def __init__(self) -> None:
        self._email = os.environ.get("CROSSREF_EMAIL", "").strip() or None

    def _headers(self) -> dict[str, str]:
        """Build request headers (including mailto for polite pool)."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._email:
            headers["User-Agent"] = (
                f"arxiv-mcp-server/1.0 (mailto:{self._email})"
            )
        return headers

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
            Parsed JSON ``message`` field from the Crossref response.

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP errors.
            ValueError: If resource not found (404).
        """
        await crossref_limiter.wait()
        url = f"{CROSSREF_BASE_URL}{path}"

        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url, params=params, headers=self._headers()
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("message", data)

                if response.status_code == 404:
                    raise ValueError(f"DOI or resource not found on Crossref: {path}")

                if response.status_code == 429:
                    delay = 2 ** attempt
                    logger.warning(
                        f"Crossref rate limited (429), retrying in {delay}s "
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

    async def get_work_by_doi(self, doi: str) -> dict[str, Any]:
        """Resolve a DOI to full metadata.

        Args:
            doi: DOI string, e.g. ``10.1234/example``.

        Returns:
            Normalized paper dict.
        """
        # Strip any URL prefix
        clean_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        result = await self._request(f"/works/{clean_doi}")
        return _normalize_work(result)

    async def search_works(
        self,
        query: str,
        rows: int = 25,
    ) -> list[dict[str, Any]]:
        """Search Crossref works by query string.

        Args:
            query: Free-text search query.
            rows: Number of results to return (max 1000).

        Returns:
            List of normalized paper dicts.
        """
        params: dict[str, Any] = {
            "query": query,
            "rows": min(rows, 1000),
        }
        result = await self._request("/works", params=params)
        items = result.get("items", [])
        return [_normalize_work(item) for item in items]

    async def get_references(self, doi: str) -> list[dict[str, Any]]:
        """Get the reference list for a paper (what it cites).

        Crossref embeds references in the work record itself.

        Args:
            doi: DOI of the paper whose references to retrieve.

        Returns:
            List of normalized paper dicts for references. Note that
            Crossref reference records are often sparse (DOI + unstructured string).
        """
        clean_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        result = await self._request(f"/works/{clean_doi}")
        references = result.get("reference", [])

        normalized: list[dict[str, Any]] = []
        for ref in references:
            # Crossref references are minimal — extract what we can
            ref_doi = ref.get("DOI")
            normalized.append({
                "id": ref_doi or ref.get("key", ""),
                "source": "crossref",
                "source_id": ref_doi,
                "doi": ref_doi,
                "title": ref.get("article-title", ref.get("unstructured", "")),
                "authors": [ref["author"]] if ref.get("author") else [],
                "abstract": "",
                "published_date": ref.get("year"),
                "citation_count": None,
                "categories": [],
                "url": f"https://doi.org/{ref_doi}" if ref_doi else None,
                "open_access": False,
                "venue": ref.get("journal-title"),
            })

        return normalized
