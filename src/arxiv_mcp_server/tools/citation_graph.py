"""Citation graph tool using Semantic Scholar API."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List
from urllib.parse import quote

import httpx
import mcp.types as types
from mcp.types import ToolAnnotations

logger = logging.getLogger("arxiv-mcp-server")

SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper"

citation_graph_tool = types.Tool(
    name="citation_graph",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Return papers citing an arXiv paper and papers that it references "
        "using Semantic Scholar's citation graph."
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
                "description": "Pagination offset applied to both directions.",
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
    s2_paper_identifier = quote(f"ARXIV:{paper_id}")

    # Request order (matches the test's side_effect ordering):
    #   1. root paper metadata
    #   2. /citations page
    #   3. /references page
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
        root_response = await client.get(root_url)
        root_response.raise_for_status()
        citations_response = await client.get(citations_url)
        citations_response.raise_for_status()
        references_response = await client.get(references_url)
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
        root_external_ids = root_payload.get("externalIds") or {}
        paper = {
            "paper_id": root_payload.get("paperId"),
            "arxiv_id": root_external_ids.get("ArXiv") or paper_id,
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

    result = {
        "status": "success",
        "paper": paper,
        "citation_count": len(citations),
        "reference_count": len(references),
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
        compact = bool(arguments.get("compact", False))

        if not (limit is None and offset is None and not compact):
            page_limit = limit if limit is not None else 100
            page_offset = offset or 0
            return await _handle_citation_graph_paginated(
                paper_id, page_limit, page_offset, compact
            )

        s2_paper_identifier = quote(f"ARXIV:{paper_id}")
        fields = (
            "title,year,authors,externalIds,"
            "citations.paperId,citations.title,citations.year,citations.authors,citations.externalIds,"
            "references.paperId,references.title,references.year,references.authors,references.externalIds"
        )

        url = f"{SEMANTIC_SCHOLAR_BASE_URL}/{s2_paper_identifier}?fields={fields}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

        payload = response.json()
        citations = _normalize_paper_items(payload.get("citations", []))
        references = _normalize_paper_items(payload.get("references", []))

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
            "citations": citations,
            "references": references,
        }

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
