"""Citation graph tool using Semantic Scholar API."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Dict, List
from urllib.parse import quote

import httpx
import mcp.types as types
from mcp.types import ToolAnnotations

from ..config import Settings
from .arxiv_ids import (
    arxiv_version_suffix,
    bare_arxiv_id,
    is_valid_arxiv_id,
    normalize_arxiv_id,
)

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1"
DEFAULT_MAX_CITATIONS = 50
MAX_CITATIONS_CAP = 200
PAPER_FIELDS = "title,year,authors,externalIds,citationCount,referenceCount"
# Include externalIds so neighbors can expose arxiv_id for download/get_abstract
# hops. Keep default max_citations=50 and 429 retries to stay under quota.
NEIGHBOR_FIELDS = "paperId,title,year,authors,externalIds"
RATE_LIMIT_MESSAGE = (
    "Semantic Scholar rate-limited this request. "
    "Set the SEMANTIC_SCHOLAR_API_KEY environment variable for a higher quota, "
    "or retry later with a smaller max_citations."
)
RATE_LIMIT_MESSAGE_WITH_KEY = (
    "Semantic Scholar rate-limited this request despite SEMANTIC_SCHOLAR_API_KEY. "
    "Wait and retry, or reduce max_citations."
)
_MAX_RETRIES = 5
_INITIAL_BACKOFF_SECONDS = 2.0
_MAX_BACKOFF_SECONDS = 60.0

citation_graph_tool = types.Tool(
    name="citation_graph",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Return papers citing an arXiv paper and papers that it references "
        "using Semantic Scholar's citation graph. Results are bounded "
        "(default 50) to stay within the unauthenticated quota. Under load, "
        "export SEMANTIC_SCHOLAR_API_KEY for a higher limit; without a key, "
        "persistent rate limits return status=rate_limited instead of failing hard."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "arXiv ID (for example: 2401.12345).",
            },
            "max_citations": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_CITATIONS_CAP,
                "description": (
                    "Maximum citations and references to return "
                    f"(default {DEFAULT_MAX_CITATIONS})."
                ),
            },
        },
        "required": ["paper_id"],
        "additionalProperties": False,
    },
)


class SemanticScholarRateLimitError(Exception):
    """Raised when Semantic Scholar keeps returning HTTP 429."""


def _normalize_paper_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize paper lists returned by Semantic Scholar."""
    normalized: List[Dict[str, Any]] = []
    for item in items:
        paper_id = item.get("paperId")
        title = item.get("title", "")
        year = item.get("year")
        external_ids = item.get("externalIds") or {}
        authors = [author.get("name", "") for author in item.get("authors", [])]

        normalized.append(
            {
                "paper_id": paper_id,
                "title": title,
                "year": year,
                "authors": authors,
                "external_ids": external_ids,
                "arxiv_id": external_ids.get("ArXiv"),
            }
        )

    return normalized


def _s2_headers() -> Dict[str, str]:
    """Build Semantic Scholar request headers, including an optional API key."""
    headers = {
        "User-Agent": (
            f"{settings.APP_NAME}/{settings.APP_VERSION} "
            "(https://github.com/blazickjp/arxiv-mcp-server; research tool)"
        )
    }
    api_key = (settings.SEMANTIC_SCHOLAR_API_KEY or "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _has_api_key() -> bool:
    """Return True when SEMANTIC_SCHOLAR_API_KEY is configured."""
    return bool((settings.SEMANTIC_SCHOLAR_API_KEY or "").strip())


def _rate_limit_message() -> str:
    """Actionable guidance for Semantic Scholar 429 responses."""
    if _has_api_key():
        return RATE_LIMIT_MESSAGE_WITH_KEY
    return RATE_LIMIT_MESSAGE


def _rate_limited_payload(
    *,
    arxiv_id: str | None = None,
    max_citations: int | None = None,
) -> Dict[str, Any]:
    """Soft rate-limit result so callers can continue without a hard tool error."""
    payload: Dict[str, Any] = {
        "status": "rate_limited",
        "message": _rate_limit_message(),
        "citation_count": 0,
        "reference_count": 0,
        "citations": [],
        "references": [],
    }
    if arxiv_id is not None:
        payload["arxiv_id"] = arxiv_id
    if max_citations is not None:
        payload["max_citations"] = max_citations
    if not _has_api_key():
        payload["hint"] = "export SEMANTIC_SCHOLAR_API_KEY=<your-key>"
    return payload


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    """Exponential backoff with jitter, honoring numeric Retry-After when present."""
    delay = min(_INITIAL_BACKOFF_SECONDS * (2**attempt), _MAX_BACKOFF_SECONDS)
    if retry_after:
        try:
            delay = min(max(delay, float(retry_after)), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    # Full-ish jitter keeps concurrent clients from retrying in lockstep.
    jittered = delay * (0.5 + random.random())
    return min(jittered, _MAX_BACKOFF_SECONDS)


async def _s2_get(
    client: httpx.AsyncClient, url: str, params: Dict[str, Any] | None = None
) -> httpx.Response:
    """GET a Semantic Scholar URL, retrying with backoff on HTTP 429."""
    for attempt in range(_MAX_RETRIES + 1):
        response = await client.get(url, params=params, headers=_s2_headers())
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if attempt == _MAX_RETRIES:
            break
        wait = _backoff_seconds(attempt, response.headers.get("Retry-After"))
        logger.warning("Semantic Scholar 429 on %s; retrying in %.1fs", url, wait)
        await asyncio.sleep(wait)
    raise SemanticScholarRateLimitError(_rate_limit_message())


def _neighbor_papers(payload: Dict[str, Any], wrapper_key: str) -> List[Dict[str, Any]]:
    """Unwrap citation/reference endpoint rows into paper objects."""
    papers: List[Dict[str, Any]] = []
    for row in payload.get("data") or []:
        paper = row.get(wrapper_key) if isinstance(row, dict) else None
        if isinstance(paper, dict):
            papers.append(paper)
    return papers


def _bound_max_citations(value: Any) -> int:
    """Clamp the caller-supplied neighbor bound to the documented range."""
    if value is None:
        return DEFAULT_MAX_CITATIONS
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CITATIONS
    return max(1, min(parsed, MAX_CITATIONS_CAP))


def _rate_limited_response(
    *,
    arxiv_id: str | None = None,
    max_citations: int | None = None,
) -> List[types.TextContent]:
    """Build the soft rate-limited tool response."""
    return [
        types.TextContent(
            type="text",
            text=json.dumps(
                _rate_limited_payload(arxiv_id=arxiv_id, max_citations=max_citations),
                indent=2,
            ),
        )
    ]


async def handle_citation_graph(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle citation graph lookup for a single arXiv paper ID."""
    bare_id: str | None = None
    limit: int | None = None
    try:
        paper_id = normalize_arxiv_id(arguments["paper_id"])
        if not paper_id:
            return [types.TextContent(type="text", text="Error: paper_id is required")]
        if not is_valid_arxiv_id(paper_id):
            return [
                types.TextContent(type="text", text="Error: invalid arXiv ID format")
            ]

        # Semantic Scholar indexes bare arXiv IDs only; strip vN for lookup.
        bare_id = bare_arxiv_id(paper_id)
        requested_version = arxiv_version_suffix(paper_id)
        limit = _bound_max_citations(arguments.get("max_citations"))
        s2_paper_identifier = quote(f"ARXIV:{bare_id}", safe=":")
        paper_url = f"{SEMANTIC_SCHOLAR_API_URL}/paper/{s2_paper_identifier}"
        citations_url = f"{paper_url}/citations"
        references_url = f"{paper_url}/references"
        neighbor_params = {"fields": NEIGHBOR_FIELDS, "limit": limit}

        async with httpx.AsyncClient(timeout=30.0) as client:
            paper_response = await _s2_get(client, paper_url, {"fields": PAPER_FIELDS})
            citations_response = await _s2_get(client, citations_url, neighbor_params)
            references_response = await _s2_get(client, references_url, neighbor_params)

        payload = paper_response.json()
        citations = _normalize_paper_items(
            _neighbor_papers(citations_response.json(), "citingPaper")
        )
        references = _normalize_paper_items(
            _neighbor_papers(references_response.json(), "citedPaper")
        )

        paper_meta = {
            "paper_id": payload.get("paperId"),
            "arxiv_id": bare_id,
            "title": payload.get("title", ""),
            "year": payload.get("year"),
            "authors": [
                author.get("name", "") for author in payload.get("authors", [])
            ],
            "external_ids": payload.get("externalIds") or {},
        }
        if requested_version is not None:
            paper_meta["requested_arxiv_id"] = paper_id
            paper_meta["requested_version"] = requested_version

        result = {
            "status": "success",
            "paper": paper_meta,
            "citation_count": len(citations),
            "reference_count": len(references),
            "citation_total": payload.get("citationCount"),
            "reference_total": payload.get("referenceCount"),
            "max_citations": limit,
            "citations": citations,
            "references": references,
        }

        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    except SemanticScholarRateLimitError as exc:
        logger.error("Semantic Scholar rate limited: %s", exc)
        return _rate_limited_response(arxiv_id=bare_id, max_citations=limit)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            logger.error("Semantic Scholar rate limited: %s", exc)
            return _rate_limited_response(arxiv_id=bare_id, max_citations=limit)
        status = exc.response.status_code if exc.response is not None else None
        # Never leak upstream status lines / URLs (issue #166 class).
        logger.error("Semantic Scholar HTTP error: status=%s", status)
        if status == 404:
            return [
                types.TextContent(
                    type="text",
                    text="Error: paper not found on Semantic Scholar",
                )
            ]
        detail = f" (HTTP {status})" if status is not None else ""
        return [
            types.TextContent(
                type="text",
                text=f"Error: Semantic Scholar API HTTP error{detail}",
            )
        ]
    except Exception as exc:
        logger.error("Citation graph error: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]
