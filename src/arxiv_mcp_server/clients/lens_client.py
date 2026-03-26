"""Lens.org API client for patent and scholarly search.

Lens.org API
Scholarly search: POST https://api.lens.org/scholarly/search
Patent search: POST https://api.lens.org/patent/search
Auth: Free API token for individuals (register at lens.org)
  Set via env var: LENS_API_TOKEN
Rate limit: 10 req/min for free tier
Docs: https://docs.api.lens.org/
"""

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("arxiv-mcp-server")

LENS_BASE_URL = "https://api.lens.org"

# Lens free tier allows 10 req/min => ~0.16 req/s
lens_limiter = RateLimiter(calls_per_second=0.16)


def _get_api_token() -> Optional[str]:
    """Get Lens.org API token from environment."""
    token = os.environ.get("LENS_API_TOKEN", "").strip()
    return token if token else None


def _normalize_scholarly(item: dict) -> dict:
    """Normalize a Lens.org scholarly result to a consistent dict.

    Args:
        item: Raw scholarly record from the Lens API.

    Returns:
        Normalized dict with standard keys.
    """
    return {
        "id": item.get("lens_id", ""),
        "source": "lens",
        "doi": item.get("external_ids", {}).get("doi"),
        "title": item.get("title", ""),
        "authors": [a.get("display_name", "") for a in item.get("authors", [])],
        "abstract": item.get("abstract", ""),
        "published_date": item.get("date_published"),
        "citation_count": item.get("scholarly_citations_count"),
        "source_title": item.get("source", {}).get("title"),
        "open_access": item.get("is_open_access", False),
    }


def _normalize_patent(item: dict) -> dict:
    """Normalize a Lens.org patent result to a consistent dict.

    Args:
        item: Raw patent record from the Lens API.

    Returns:
        Normalized dict with standard keys.
    """
    invention_titles = item.get("biblio", {}).get("invention_title", [{}])
    title = invention_titles[0].get("text", "") if invention_titles else ""

    applicants = (
        item.get("biblio", {}).get("parties", {}).get("applicants", [])
    )
    applicant_names = [
        a.get("extracted_name", {}).get("value", "") for a in applicants
    ]

    claims = item.get("claims", {}).get("claims", [])

    return {
        "id": item.get("lens_id", ""),
        "source": "lens_patent",
        "title": title,
        "applicants": applicant_names,
        "filing_date": item.get("date_published"),
        "patent_number": item.get("doc_number"),
        "jurisdiction": item.get("jurisdiction"),
        "url": f"https://www.lens.org/lens/patent/{item.get('lens_id', '')}",
        "claims_count": len(claims),
    }


class LensClient:
    """Async client for the Lens.org API (scholarly + patent search)."""

    def __init__(self) -> None:
        self._api_token = _get_api_token()

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    def has_token(self) -> bool:
        """Check whether an API token is configured."""
        return self._api_token is not None

    async def _request(
        self,
        path: str,
        *,
        json_body: dict[str, Any],
        max_retries: int = 3,
    ) -> Any:
        """Make a POST request with rate limiting and retry logic.

        Args:
            path: URL path (appended to base URL).
            json_body: JSON body for the POST request.
            max_retries: Max retries on 429.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP errors.
            ValueError: If the API returns an error response.
        """
        await lens_limiter.wait()
        url = f"{LENS_BASE_URL}{path}"

        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url, json=json_body, headers=self._headers()
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429:
                    delay = 2 ** attempt
                    logger.warning(
                        "Lens rate limited (429), retrying in %ds "
                        "(attempt %d/%d)",
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                if response.status_code in (400, 401, 403):
                    raise ValueError(
                        f"Lens API error ({response.status_code}): "
                        f"{response.text}"
                    )

                response.raise_for_status()

        raise httpx.HTTPStatusError(
            "Max retries exceeded on 429",
            request=httpx.Request("POST", url),
            response=response,  # type: ignore[possibly-undefined]
        )

    async def search_scholarly(
        self,
        query: str,
        limit: int = 20,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Search Lens.org scholarly works.

        Args:
            query: Search query text.
            limit: Max results to return (default 20).
            date_from: Optional start date (YYYY-MM-DD).
            date_to: Optional end date (YYYY-MM-DD).

        Returns:
            List of normalized scholarly result dicts.
        """
        body: dict[str, Any] = {
            "query": {"match": {"title": query}},
            "size": min(limit, 50),
            "sort": [{"relevancy_score": "desc"}],
        }

        # Add date range filter if provided
        if date_from or date_to:
            date_range: dict[str, str] = {}
            if date_from:
                date_range["gte"] = date_from
            if date_to:
                date_range["lte"] = date_to
            body["query"] = {
                "bool": {
                    "must": [
                        {"match": {"title": query}},
                        {"range": {"date_published": date_range}},
                    ]
                }
            }

        result = await self._request("/scholarly/search", json_body=body)
        raw_items = result.get("data", [])
        return [_normalize_scholarly(item) for item in raw_items]

    async def search_patents(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search Lens.org patents by claim text.

        Args:
            query: Search query text.
            limit: Max results to return (default 20).

        Returns:
            List of normalized patent result dicts.
        """
        body: dict[str, Any] = {
            "query": {"match": {"claims.claim_text": query}},
            "size": min(limit, 50),
            "sort": [{"relevancy_score": "desc"}],
        }

        result = await self._request("/patent/search", json_body=body)
        raw_items = result.get("data", [])
        return [_normalize_patent(item) for item in raw_items]

    async def get_patent(
        self,
        lens_id: str,
    ) -> dict[str, Any]:
        """Get a single patent by its Lens ID.

        Args:
            lens_id: The Lens.org patent ID.

        Returns:
            Normalized patent dict.

        Raises:
            ValueError: If the patent is not found.
        """
        body: dict[str, Any] = {
            "query": {"match": {"lens_id": lens_id}},
            "size": 1,
        }

        result = await self._request("/patent/search", json_body=body)
        items = result.get("data", [])
        if not items:
            raise ValueError(f"Patent not found: {lens_id}")
        return _normalize_patent(items[0])

    async def find_patents_citing_paper(
        self,
        doi: str,
    ) -> list[dict[str, Any]]:
        """Find patents that cite a scholarly work by DOI.

        Args:
            doi: The DOI of the scholarly work.

        Returns:
            List of normalized patent dicts that cite the paper.
        """
        body: dict[str, Any] = {
            "query": {
                "match": {"scholarly_citations.doi": doi},
            },
            "size": 20,
            "sort": [{"relevancy_score": "desc"}],
        }

        result = await self._request("/patent/search", json_body=body)
        raw_items = result.get("data", [])
        return [_normalize_patent(item) for item in raw_items]
