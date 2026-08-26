"""Citation graph tool using Semantic Scholar API."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path
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
# Request paper metadata plus up to limit citations/references in one call.
# Subfield syntax (citations.year, references.externalIds) lets neighbors expose
# arxiv_id for tool hops while keeping responses small.
PAPER_FIELDS = (
    "title,year,authors,externalIds,citationCount,referenceCount,citations,references"
)
CITATION_SUBFIELDS = "citations.paperId,citations.title,citations.year,citations.authors,citations.externalIds"
REFERENCE_SUBFIELDS = "references.paperId,references.title,references.year,references.authors,references.externalIds"
RATE_LIMIT_MESSAGE = (
    "⚠️ RATE LIMITED: Semantic Scholar quota exhausted. "
    "This is NOT an empty graph—the API blocked the request. "
    "Get a free API key at https://www.semanticscholar.org/product/api#api-key "
    "and set SEMANTIC_SCHOLAR_API_KEY, or retry later with a smaller max_citations."
)
RATE_LIMIT_MESSAGE_WITH_KEY = (
    "⚠️ RATE LIMITED: Semantic Scholar quota exhausted despite SEMANTIC_SCHOLAR_API_KEY. "
    "This is NOT an empty graph—the API blocked the request. "
    "Wait and retry, or reduce max_citations."
)
_MAX_RETRIES = 5
_INITIAL_BACKOFF_SECONDS = 2.0
_MAX_BACKOFF_SECONDS = 60.0
# Cache citation graphs on disk to reduce repeated S2 calls. Rate-limited results
# expire quickly; successful graphs persist longer.
CACHE_TTL_SUCCESS_SECONDS = 7 * 24 * 3600  # 7 days
CACHE_TTL_RATE_LIMITED_SECONDS = 5 * 60  # 5 minutes

citation_graph_tool = types.Tool(
    name="citation_graph",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Return papers citing an arXiv paper and papers that it references "
        "using Semantic Scholar's citation graph. Results are bounded "
        "(default 50) to stay within the unauthenticated quota. Graphs are "
        "cached on disk to reduce repeated API calls. Under load, "
        "a free Semantic Scholar API key (https://www.semanticscholar.org/product/api#api-key) "
        "makes this reliable; without a key, persistent rate limits return "
        "status=rate_limited with an unmistakable warning instead of failing hard."
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
        "error": "RATE_LIMITED",
        "message": _rate_limit_message(),
        "warning": "This is NOT an empty citation graph. The API request was blocked by rate limiting.",
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
        payload["hint"] = (
            "Get a free API key at https://www.semanticscholar.org/product/api#api-key and set SEMANTIC_SCHOLAR_API_KEY"
        )
    return payload


def _cache_dir() -> Path:
    """Return the citation graph cache directory under the configured storage path."""
    cache_path = settings.STORAGE_PATH / "citation_graphs"
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


def _cache_key(arxiv_id: str, max_citations: int) -> str:
    """Build a cache filename for this arXiv ID and max_citations."""
    # Normalize to bare ID for consistent cache keys
    bare_id = bare_arxiv_id(arxiv_id)
    return f"{bare_id}__limit_{max_citations}.json"


def _load_cached_graph(arxiv_id: str, max_citations: int) -> Dict[str, Any] | None:
    """Load a cached citation graph if valid and not expired.

    This function looks for a cache file that matches the arxiv_id and has
    max_citations >= requested max_citations. If multiple cache files exist,
    it prefers the one closest to the requested limit.
    """
    cache_dir_path = _cache_dir()
    bare_id = bare_arxiv_id(arxiv_id)

    # Find all cache files for this arxiv_id
    pattern = f"{bare_id}__limit_*.json"
    import glob

    cache_files = glob.glob(str(cache_dir_path / pattern))

    if not cache_files:
        return None

    # Find a suitable cache file (with limit >= requested)
    best_cache = None
    best_limit = float("inf")

    for cache_file in cache_files:
        try:
            # Extract limit from filename
            filename = Path(cache_file).stem
            limit_str = filename.split("__limit_")[1]
            cached_limit = int(limit_str)

            # Check if this cache can satisfy the request
            if cached_limit >= max_citations and cached_limit < best_limit:
                best_cache = cache_file
                best_limit = cached_limit
        except (IndexError, ValueError):
            continue

    if best_cache is None:
        logger.debug(
            "No suitable cache found for %s with limit %d", bare_id, max_citations
        )
        return None

    try:
        with open(best_cache, "r", encoding="utf-8") as f:
            cached = json.load(f)

        cached_at = cached.get("cached_at", 0)
        status = cached.get("status", "success")
        now = time.time()

        # Rate-limited results expire quickly; successful graphs persist longer
        if status == "rate_limited":
            ttl = CACHE_TTL_RATE_LIMITED_SECONDS
        else:
            ttl = CACHE_TTL_SUCCESS_SECONDS

        if now - cached_at > ttl:
            logger.debug(
                "Cache expired for %s (age %.1fs)",
                Path(best_cache).name,
                now - cached_at,
            )
            return None

        logger.info(
            "Cache hit for %s (age %.1fs)", Path(best_cache).name, now - cached_at
        )

        # If we cached more than requested, slice the results.
        # Work on a copy to avoid issues with json.load() returning shared lists.
        cached_limit = cached.get("max_citations", DEFAULT_MAX_CITATIONS)
        if cached_limit > max_citations and status == "success":
            import copy

            result = copy.deepcopy(cached)
            result["citations"] = result.get("citations", [])[:max_citations]
            result["references"] = result.get("references", [])[:max_citations]
            result["citation_count"] = len(result["citations"])
            result["reference_count"] = len(result["references"])
            result["max_citations"] = max_citations
            return result

        return cached

    except Exception as exc:
        logger.warning("Failed to load cache %s: %s", Path(best_cache).name, exc)
        return None


def _save_cached_graph(
    arxiv_id: str, max_citations: int, result: Dict[str, Any]
) -> None:
    """Save a citation graph result to disk cache."""
    cache_file = _cache_dir() / _cache_key(arxiv_id, max_citations)
    try:
        # Create a copy to avoid mutating the original result
        cached_result = result.copy()
        cached_result["cached_at"] = time.time()
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cached_result, f, indent=2)
        logger.debug("Cached citation graph to %s", cache_file.name)
    except Exception as exc:
        logger.warning("Failed to cache citation graph to %s: %s", cache_file.name, exc)


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


def _extract_citations(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract citations list from paper endpoint response."""
    citations = payload.get("citations", {})
    if isinstance(citations, dict):
        # Citations returned as {data: [...]}
        return citations.get("data", [])
    elif isinstance(citations, list):
        # Citations returned as direct list
        return citations
    return []


def _extract_references(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract references list from paper endpoint response."""
    references = payload.get("references", {})
    if isinstance(references, dict):
        # References returned as {data: [...]}
        return references.get("data", [])
    elif isinstance(references, list):
        # References returned as direct list
        return references
    return []


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
    """Handle citation graph lookup for a single arXiv paper ID.

    This function:
    1. Checks the disk cache first to avoid redundant S2 API calls
    2. Makes ONE S2 API call to fetch paper + citations + references together
    3. Caches the result for future lookups
    4. Returns unmistakable rate-limit responses on 429

    Args:
        arguments: Tool arguments with paper_id (required) and max_citations (optional).

    Returns:
        List of TextContent with the citation graph or error.
    """
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

        # Check cache first
        cached = _load_cached_graph(bare_id, limit)
        if cached is not None:
            # Restore requested version metadata if present
            if requested_version is not None and "paper" in cached:
                cached["paper"]["requested_arxiv_id"] = paper_id
                cached["paper"]["requested_version"] = requested_version
            return [types.TextContent(type="text", text=json.dumps(cached, indent=2))]

        # Cache miss: fetch from S2 with a single API call
        s2_paper_identifier = quote(f"ARXIV:{bare_id}", safe=":")
        paper_url = f"{SEMANTIC_SCHOLAR_API_URL}/paper/{s2_paper_identifier}"

        # Request paper metadata plus citations and references with subfields.
        # This reduces from 3 sequential calls to 1.
        fields = f"{PAPER_FIELDS},{CITATION_SUBFIELDS},{REFERENCE_SUBFIELDS}"
        params = {
            "fields": fields,
            # Note: The paper endpoint does not support limit/offset for nested
            # citations/references fields. S2 returns a bounded subset (typically
            # up to 1000 per field). For our default 50 / cap 200 use case, this
            # is sufficient. If we need precise control, we'd fall back to separate
            # /citations and /references calls, but that defeats the quota goal.
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            paper_response = await _s2_get(client, paper_url, params)

        payload = paper_response.json()

        # Extract citations and references from the unified response
        citations_raw = _extract_citations(payload)
        references_raw = _extract_references(payload)

        # Semantic Scholar may return more than we requested since we can't pass
        # limit to the paper endpoint's nested fields. Slice to the requested limit.
        citations = _normalize_paper_items(citations_raw[:limit])
        references = _normalize_paper_items(references_raw[:limit])

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

        # Cache the successful result
        _save_cached_graph(bare_id, limit, result)

        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    except SemanticScholarRateLimitError as exc:
        logger.error("Semantic Scholar rate limited: %s", exc)
        rate_limited = _rate_limited_payload(arxiv_id=bare_id, max_citations=limit)
        # Cache rate-limited results with a short TTL so we don't hammer S2
        if bare_id is not None and limit is not None:
            _save_cached_graph(bare_id, limit, rate_limited)
        return _rate_limited_response(arxiv_id=bare_id, max_citations=limit)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            logger.error("Semantic Scholar rate limited: %s", exc)
            rate_limited = _rate_limited_payload(arxiv_id=bare_id, max_citations=limit)
            if bare_id is not None and limit is not None:
                _save_cached_graph(bare_id, limit, rate_limited)
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
