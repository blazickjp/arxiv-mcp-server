"""DBLP computer science bibliography client.

DBLP API endpoints:
    Search publications: GET https://dblp.org/search/publ/api?q=query&format=json&h=max_results
    Search authors:      GET https://dblp.org/search/author/api?q=name&format=json
    Search venues:       GET https://dblp.org/search/venue/api?q=name&format=json

Auth: None required.
Rate limit: Be polite, ~1 req/s.
"""

import logging
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("arxiv-mcp-server")

DBLP_BASE_URL = "https://dblp.org"

dblp_limiter = RateLimiter(calls_per_second=1)


def _normalize_publication(hit: dict[str, Any]) -> dict[str, Any]:
    """Normalize a DBLP publication hit into a consistent dict.

    Args:
        hit: Raw hit dict from DBLP search response.

    Returns:
        Normalized publication dict.
    """
    info = hit.get("info", {})
    authors = info.get("authors", {}).get("author", [])
    if isinstance(authors, dict):
        authors = [authors]  # single author case

    return {
        "id": info.get("key", ""),
        "source": "dblp",
        "title": info.get("title", ""),
        "authors": [
            a.get("text", a) if isinstance(a, dict) else a for a in authors
        ],
        "venue": info.get("venue", ""),
        "year": info.get("year"),
        "doi": info.get("doi"),
        "url": info.get("ee", info.get("url")),
        "type": info.get("type", ""),  # article, inproceedings, etc.
    }


def _normalize_author(hit: dict[str, Any]) -> dict[str, Any]:
    """Normalize a DBLP author hit.

    Args:
        hit: Raw hit dict from DBLP author search.

    Returns:
        Normalized author dict.
    """
    info = hit.get("info", {})
    return {
        "name": info.get("author", ""),
        "url": info.get("url", ""),
        "notes": info.get("notes", {}),
    }


def _normalize_venue(hit: dict[str, Any]) -> dict[str, Any]:
    """Normalize a DBLP venue hit.

    Args:
        hit: Raw hit dict from DBLP venue search.

    Returns:
        Normalized venue dict.
    """
    info = hit.get("info", {})
    return {
        "venue": info.get("venue", ""),
        "acronym": info.get("acronym", ""),
        "type": info.get("type", ""),
        "url": info.get("url", ""),
    }


class DBLPClient:
    """Async client for the DBLP computer science bibliography API."""

    async def _request(
        self,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Make a rate-limited GET request to the DBLP API.

        Args:
            path: API path (appended to base URL).
            params: Query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: On HTTP errors.
        """
        await dblp_limiter.wait()
        url = f"{DBLP_BASE_URL}{path}"
        params["format"] = "json"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def search_publications(
        self,
        query: str,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Search CS publications on DBLP.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.

        Returns:
            List of normalized publication dicts with title, authors,
            venue, year, DOI, URL, etc.
        """
        data = await self._request(
            "/search/publ/api",
            params={"q": query, "h": max_results},
        )

        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        return [_normalize_publication(h) for h in hits]

    async def search_authors(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for authors on DBLP.

        Args:
            query: Author name to search for.
            max_results: Maximum number of results to return.

        Returns:
            List of author dicts with name, url, and notes.
        """
        data = await self._request(
            "/search/author/api",
            params={"q": query, "h": max_results},
        )

        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        return [_normalize_author(h) for h in hits]

    async def search_venues(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for venues (conferences/journals) on DBLP.

        Args:
            query: Venue name to search for.
            max_results: Maximum number of results to return.

        Returns:
            List of venue dicts with venue name, acronym, type, and url.
        """
        data = await self._request(
            "/search/venue/api",
            params={"q": query, "h": max_results},
        )

        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        return [_normalize_venue(h) for h in hits]

    async def get_author_publications(
        self,
        author_url: str,
        max_results: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Get all publications by an author given their DBLP profile URL.

        Args:
            author_url: DBLP author profile URL (e.g., https://dblp.org/pid/h/GeoffreyEHinton).
            max_results: Optional cap on number of results.

        Returns:
            List of normalized publication dicts.
        """
        await dblp_limiter.wait()

        # DBLP author pages serve JSON when requested with .json suffix
        url = author_url.rstrip("/") + ".xml"
        # Use the search API instead for more reliable JSON output
        # Extract author key from URL for search
        api_url = f"{author_url}.json"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(api_url, follow_redirects=True)
            response.raise_for_status()
            data = response.json()

        # DBLP author JSON returns publications under "r" key
        pubs_raw = data.get("r", [])
        if not isinstance(pubs_raw, list):
            pubs_raw = [pubs_raw]

        results: list[dict[str, Any]] = []
        for entry in pubs_raw:
            if max_results is not None and len(results) >= max_results:
                break

            # Each entry has a publication type key (article, inproceedings, etc.)
            for pub_type in (
                "article", "inproceedings", "proceedings", "book",
                "incollection", "phdthesis", "mastersthesis",
            ):
                pub = entry.get(pub_type)
                if pub is not None:
                    authors_raw = pub.get("authors", {}).get("author", [])
                    if isinstance(authors_raw, dict):
                        authors_raw = [authors_raw]
                    if isinstance(authors_raw, str):
                        authors_raw = [{"text": authors_raw}]

                    results.append({
                        "id": pub.get("key", ""),
                        "source": "dblp",
                        "title": pub.get("title", ""),
                        "authors": [
                            a.get("text", a) if isinstance(a, dict) else a
                            for a in authors_raw
                        ],
                        "venue": pub.get("journal", pub.get("booktitle", "")),
                        "year": pub.get("year"),
                        "doi": pub.get("doi"),
                        "url": pub.get("ee"),
                        "type": pub_type,
                    })
                    break

        return results
