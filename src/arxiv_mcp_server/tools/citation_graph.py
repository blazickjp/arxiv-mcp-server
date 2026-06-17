"""Citation graph tool using Semantic Scholar API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
import mcp.types as types
from mcp.types import ToolAnnotations

from ..config import Settings

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper"


async def _s2_get(client, url, *, headers=None, max_retries=4, base_delay=1.0):
    """GET with exponential backoff on HTTP 429 (S2 unauthenticated rate limit).

    Honors a numeric Retry-After header when present. Returns the final response
    (caller still calls raise_for_status())."""
    response = await client.get(url, headers=headers or {})
    for attempt in range(max_retries):
        if response.status_code != 429:
            return response
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None and str(retry_after).isdigit():
            delay = float(retry_after)
        else:
            delay = base_delay * (2**attempt)
        await asyncio.sleep(delay)
        response = await client.get(url, headers=headers or {})
    return response


def _apply_edge_cap(
    citations: List[Dict[str, Any]],
    references: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    """Truncate edge lists to settings.CITATION_MAX_EDGES (per direction) when
    configured. Returns (citations, references, truncated: bool)."""
    cap: Optional[int] = settings.CITATION_MAX_EDGES
    if cap is None:
        return citations, references, False
    truncated = len(citations) > cap or len(references) > cap
    return citations[:cap], references[:cap], truncated


citation_graph_tool = types.Tool(
    name="citation_graph",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Return papers citing an arXiv paper and papers that it references "
        "using Semantic Scholar's citation graph. In paginated mode "
        "(`limit` or `compact` set), `citation_count`/`reference_count` report "
        "edges returned in the current page; each direction has its own cursor "
        "(`pagination.citations.next` / `pagination.references.next`) to pass as "
        "the next `offset`."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "arXiv ID (for example: 2401.12345).",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "description": (
                    "Max edges per direction (opt-in pagination; uses Semantic "
                    "Scholar's paginated endpoints). Omit for legacy full output."
                ),
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Pagination offset (applies only together with `limit` or "
                    "`compact`)."
                ),
            },
            "compact": {
                "type": "boolean",
                "description": (
                    "Drop author lists and nested external_ids, return minified "
                    "id+title edges (lower token cost)."
                ),
            },
        },
        "required": ["paper_id"],
        "additionalProperties": False,
    },
)


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


def _normalize_paper_items_compact(
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Normalize paper lists into compact edges (no authors/external_ids)."""
    normalized: List[Dict[str, Any]] = []
    for item in items:
        external_ids = item.get("externalIds") or {}
        normalized.append(
            {
                "paper_id": item.get("paperId"),
                "arxiv_id": external_ids.get("ArXiv"),
                "title": item.get("title", ""),
                "year": item.get("year"),
            }
        )
    return normalized


async def _handle_citation_graph_paginated(
    paper_id: str,
    page_limit: int,
    page_offset: int,
    compact: bool,
) -> List[types.TextContent]:
    """Opt-in paginated/compact path using Semantic Scholar's dedicated endpoints."""
    # Defense-in-depth: the schema bounds (1..1000, >=0) are enforced only by the
    # MCP SDK validator. Coerce + clamp per-param here so the handler never trusts
    # the input blindly. NOTE: no `limit + offset <= 1000` sum-clamp — that claim
    # was tested against the live Semantic Scholar API and refuted.
    page_limit = max(1, min(1000, int(page_limit)))
    page_offset = max(0, int(page_offset))

    s2_paper_identifier = quote(f"ARXIV:{paper_id}", safe="")

    # Three sequential requests: root paper metadata, then the /citations and
    # /references pages.
    root_url = (
        f"{SEMANTIC_SCHOLAR_BASE_URL}/{s2_paper_identifier}"
        "?fields=title,year,authors,externalIds"
    )
    page_fields = "title,year,authors,externalIds"
    citations_url = (
        f"{SEMANTIC_SCHOLAR_BASE_URL}/{s2_paper_identifier}/citations"
        f"?fields={page_fields}&limit={page_limit}&offset={page_offset}"
    )
    references_url = (
        f"{SEMANTIC_SCHOLAR_BASE_URL}/{s2_paper_identifier}/references"
        f"?fields={page_fields}&limit={page_limit}&offset={page_offset}"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        root_response = await _s2_get(client, root_url)
        root_response.raise_for_status()
        citations_response = await _s2_get(client, citations_url)
        citations_response.raise_for_status()
        references_response = await _s2_get(client, references_url)
        references_response.raise_for_status()

    root_payload = root_response.json()
    citations_payload = citations_response.json()
    references_payload = references_response.json()

    citation_items = [
        entry.get("citingPaper", {}) for entry in citations_payload.get("data", [])
    ]
    reference_items = [
        entry.get("citedPaper", {}) for entry in references_payload.get("data", [])
    ]

    if compact:
        citations = _normalize_paper_items_compact(citation_items)
        references = _normalize_paper_items_compact(reference_items)
        # arxiv_id echoes the input id on every path (legacy + both paginated
        # branches) for a consistent paper.arxiv_id contract.
        paper = {
            "paper_id": root_payload.get("paperId"),
            "arxiv_id": paper_id,
            "title": root_payload.get("title", ""),
            "year": root_payload.get("year"),
        }
    else:
        citations = _normalize_paper_items(citation_items)
        references = _normalize_paper_items(reference_items)
        paper = {
            "paper_id": root_payload.get("paperId"),
            "arxiv_id": paper_id,
            "title": root_payload.get("title", ""),
            "year": root_payload.get("year"),
            "authors": [
                author.get("name", "") for author in root_payload.get("authors", [])
            ],
            "external_ids": root_payload.get("externalIds", {}),
        }

    citations, references, truncated = _apply_edge_cap(citations, references)

    result = {
        "status": "success",
        "paper": paper,
        "citation_count": len(citations),
        "reference_count": len(references),
    }
    if truncated:
        result["truncated"] = True
    result.update(
        {
            "citations": citations,
            "references": references,
            "pagination": {
                "limit": page_limit,
                "citations": {
                    "offset": page_offset,
                    "next": citations_payload.get("next"),
                    "returned": len(citations),
                },
                "references": {
                    "offset": page_offset,
                    "next": references_payload.get("next"),
                    "returned": len(references),
                },
            },
        }
    )

    if compact:
        text = json.dumps(result, separators=(",", ":"))
    else:
        text = json.dumps(result, indent=2)

    return [types.TextContent(type="text", text=text)]


async def handle_citation_graph(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle citation graph lookup for a single arXiv paper ID."""
    try:
        paper_id = arguments["paper_id"].strip()
        if not paper_id:
            return [types.TextContent(type="text", text="Error: paper_id is required")]

        limit = arguments.get("limit")
        offset = arguments.get("offset")
        # Strict bool: only a JSON `true` enables compact. Guards against a
        # truthy non-bool (e.g. the string "false") from a non-validating client.
        compact = arguments.get("compact") is True

        # Pagination is triggered only by `limit` or `compact`. `offset` alone is
        # a no-op here (it falls through to the legacy path, which has no paging);
        # it is honored only as a modifier when already paginating.
        if limit is not None or compact:
            page_limit = limit if limit is not None else 100
            page_offset = offset or 0
            return await _handle_citation_graph_paginated(
                paper_id, page_limit, page_offset, compact
            )

        s2_paper_identifier = quote(f"ARXIV:{paper_id}", safe="")
        fields = (
            "title,year,authors,externalIds,"
            "citations.paperId,citations.title,citations.year,citations.authors,citations.externalIds,"
            "references.paperId,references.title,references.year,references.authors,references.externalIds"
        )

        url = f"{SEMANTIC_SCHOLAR_BASE_URL}/{s2_paper_identifier}?fields={fields}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await _s2_get(client, url)
            response.raise_for_status()

        payload = response.json()
        citations = _normalize_paper_items(payload.get("citations", []))
        references = _normalize_paper_items(payload.get("references", []))

        citations, references, truncated = _apply_edge_cap(citations, references)

        result = {
            "status": "success",
            "paper": {
                "paper_id": payload.get("paperId"),
                "arxiv_id": paper_id,
                "title": payload.get("title", ""),
                "year": payload.get("year"),
                "authors": [
                    author.get("name", "") for author in payload.get("authors", [])
                ],
                "external_ids": payload.get("externalIds", {}),
            },
            "citation_count": len(citations),
            "reference_count": len(references),
        }
        if truncated:
            result["truncated"] = True
        result["citations"] = citations
        result["references"] = references

        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    except httpx.HTTPStatusError as exc:
        logger.error("Semantic Scholar HTTP error: %s", exc)
        return [
            types.TextContent(
                type="text",
                text=f"Error: Semantic Scholar API HTTP error - {str(exc)}",
            )
        ]
    except Exception as exc:
        logger.error("Citation graph error: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]
