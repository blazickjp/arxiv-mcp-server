"""Citation graph tool using Semantic Scholar API."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List
from urllib.parse import quote

import httpx
import mcp.types as types

logger = logging.getLogger("arxiv-mcp-server")

SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper"

citation_graph_tool = types.Tool(
    name="citation_graph",
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
            }
        },
        "required": ["paper_id"],
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


async def handle_citation_graph(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle citation graph lookup for a single arXiv paper ID."""
    try:
        paper_id = arguments["paper_id"].strip()
        if not paper_id:
            return [types.TextContent(type="text", text="Error: paper_id is required")]

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
