"""Semantic Scholar Academic Graph API client.

Base URL: https://api.semanticscholar.org/graph/v1

Key endpoints:
    GET /paper/{paper_id}                 — Paper details
    GET /paper/{paper_id}/citations       — Papers that cite this paper
    GET /paper/{paper_id}/references      — Papers this paper cites
    POST /paper/batch                     — Batch paper lookup

Paper ID formats accepted:
    - ArXiv:{arxiv_id}  (e.g., "ArXiv:2401.12345")
    - DOI:{doi}
    - S2 paper ID (40-char hex)

Rate limits:
    - Unauthenticated: 1000 req/s shared across all users
    - Authenticated (API key): 1 req/s dedicated
"""

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

from ..utils.rate_limiter import s2_limiter

logger = logging.getLogger("research-mcp-server")

S2_BASE_URL = "https://api.semanticscholar.org/graph/v1"

DEFAULT_PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,year,citationCount,"
    "influentialCitationCount,authors,venue,publicationDate,"
    "referenceCount,isOpenAccess,fieldsOfStudy"
)

DEFAULT_CITATION_FIELDS = (
    "paperId,title,year,citationCount,authors,venue,publicationDate"
)


def _get_api_key() -> Optional[str]:
    """Get S2 API key from environment."""
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    return key if key else None


def _arxiv_to_s2_id(arxiv_id: str) -> str:
    """Convert arXiv ID to S2 format.

    Strips version suffix (e.g., v2) and adds ArXiv: prefix.

    Args:
        arxiv_id: arXiv paper ID like "2401.12345" or "2401.12345v2".

    Returns:
        S2-formatted ID like "ArXiv:2401.12345".
    """
    # Strip version suffix
    clean_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
    return f"ArXiv:{clean_id}"


class S2Client:
    """Async client for the Semantic Scholar API."""

    def __init__(self) -> None:
        self._api_key = _get_api_key()

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        max_retries: int = 3,
    ) -> Any:
        """Make an API request with rate limiting and retry logic.

        Args:
            method: HTTP method.
            path: URL path (appended to base URL).
            params: Query parameters.
            json_body: JSON body for POST requests.
            max_retries: Max retries on 429.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP errors.
            ValueError: If paper not found (404).
        """
        await s2_limiter.wait()
        url = f"{S2_BASE_URL}{path}"

        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method, url, params=params, json=json_body, headers=self._headers()
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 404:
                    raise ValueError(
                        f"Paper not found on Semantic Scholar. "
                        f"Try without version suffix (e.g., '2401.12345' "
                        f"instead of '2401.12345v2')."
                    )

                if response.status_code == 429:
                    delay = 2 ** attempt
                    logger.warning(
                        f"S2 rate limited (429), retrying in {delay}s "
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

    async def get_paper(
        self,
        arxiv_id: str,
        fields: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get paper details by arXiv ID.

        Args:
            arxiv_id: arXiv paper ID.
            fields: Comma-separated S2 fields. Defaults to DEFAULT_PAPER_FIELDS.

        Returns:
            Paper details dict.
        """
        s2_id = _arxiv_to_s2_id(arxiv_id)
        params = {"fields": fields or DEFAULT_PAPER_FIELDS}
        return await self._request("GET", f"/paper/{s2_id}", params=params)

    async def get_citations(
        self,
        arxiv_id: str,
        limit: int = 20,
        fields: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get papers that cite the given paper.

        Args:
            arxiv_id: arXiv paper ID.
            limit: Max citations to return.
            fields: S2 fields for citing papers.

        Returns:
            List of citing paper dicts.
        """
        s2_id = _arxiv_to_s2_id(arxiv_id)
        params = {
            "fields": fields or DEFAULT_CITATION_FIELDS,
            "limit": min(limit, 1000),
        }
        result = await self._request(
            "GET", f"/paper/{s2_id}/citations", params=params
        )
        return [
            item["citingPaper"]
            for item in result.get("data", [])
            if item.get("citingPaper", {}).get("paperId")
        ]

    async def get_references(
        self,
        arxiv_id: str,
        limit: int = 20,
        fields: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get papers referenced by the given paper.

        Args:
            arxiv_id: arXiv paper ID.
            limit: Max references to return.
            fields: S2 fields for referenced papers.

        Returns:
            List of referenced paper dicts.
        """
        s2_id = _arxiv_to_s2_id(arxiv_id)
        params = {
            "fields": fields or DEFAULT_CITATION_FIELDS,
            "limit": min(limit, 1000),
        }
        result = await self._request(
            "GET", f"/paper/{s2_id}/references", params=params
        )
        return [
            item["citedPaper"]
            for item in result.get("data", [])
            if item.get("citedPaper", {}).get("paperId")
        ]

    async def batch_get_papers(
        self,
        arxiv_ids: list[str],
        fields: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Batch lookup multiple papers by arXiv ID.

        Args:
            arxiv_ids: List of arXiv paper IDs.
            fields: S2 fields to include.

        Returns:
            List of paper details dicts (None entries filtered out).
        """
        s2_ids = [_arxiv_to_s2_id(aid) for aid in arxiv_ids]
        params = {"fields": fields or DEFAULT_PAPER_FIELDS}
        result = await self._request(
            "POST", "/paper/batch", params=params, json_body={"ids": s2_ids}
        )
        return [p for p in result if p is not None]
