"""Browse the personal knowledge base with filters and statistics.

Lists papers with optional filtering by tags, categories, reading status,
collection, and source. Can also return KB statistics and collection info.
"""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..store.knowledge_base import KnowledgeBase

logger = logging.getLogger("research-mcp-server")

kb_list_tool = types.Tool(
    name="kb_list",
    description="""Browse your local knowledge base (papers saved via kb_save). Use to see what you have saved, check reading progress, or get KB statistics. Unlike kb_search (query-based), this is for browsing/filtering without a search query.

Filter by tags, categories, reading_status, collection, or source. Supports pagination. Set show_stats=true for paper counts, top tags, and collection info. Defaults to stats + 10 recent papers when called with no arguments.

Examples: (no args) | reading_status="unread", limit=20 | collection="thesis-refs", show_stats=true""",
    inputSchema={
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by any of these tags.",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by any of these categories (e.g., ['cs.AI', 'cs.LG']).",
            },
            "reading_status": {
                "type": "string",
                "description": "Filter by reading status.",
                "enum": ["unread", "reading", "completed", "archived"],
            },
            "collection": {
                "type": "string",
                "description": "Filter by collection name.",
            },
            "source": {
                "type": "string",
                "description": "Filter by paper source.",
                "enum": ["arxiv", "doi", "manual"],
            },
            "sort_by": {
                "type": "string",
                "description": "Sort field (default: added_at).",
                "default": "added_at",
                "enum": ["added_at", "updated_at", "title", "published_date"],
            },
            "sort_order": {
                "type": "string",
                "description": "Sort direction (default: desc).",
                "default": "desc",
                "enum": ["asc", "desc"],
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of papers to return (default: 20, max: 100).",
                "default": 20,
                "minimum": 1,
                "maximum": 100,
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N results for pagination (default: 0).",
                "default": 0,
                "minimum": 0,
            },
            "show_stats": {
                "type": "boolean",
                "description": "Include KB statistics in the response (default: false).",
                "default": False,
            },
        },
    },
)


def _format_paper_summary(paper: Dict[str, Any]) -> Dict[str, Any]:
    """Format a paper dict into a concise summary for listing."""
    notes = paper.get("notes") or ""
    notes_preview = (notes[:150] + "...") if len(notes) > 150 else notes

    return {
        "id": paper.get("id"),
        "title": paper.get("title"),
        "authors": paper.get("authors", []),
        "tags": paper.get("tags", []),
        "reading_status": paper.get("reading_status"),
        "source": paper.get("source"),
        "added_at": paper.get("added_at"),
        "notes_preview": notes_preview if notes_preview else None,
    }


async def handle_kb_list(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle knowledge base listing requests.

    Lists papers with optional filters and pagination. When no filters are
    provided and show_stats is false, defaults to showing stats plus the
    10 most recent papers.

    Args:
        arguments: Tool input with optional filters, sorting, pagination,
            and show_stats flag.

    Returns:
        List containing a single TextContent with JSON results.
    """
    try:
        tags = arguments.get("tags")
        categories = arguments.get("categories")
        reading_status = arguments.get("reading_status")
        collection = arguments.get("collection")
        source = arguments.get("source")
        sort_by = arguments.get("sort_by", "added_at")
        sort_order = arguments.get("sort_order", "desc")
        limit = min(max(int(arguments.get("limit", 20)), 1), 100)
        offset = max(int(arguments.get("offset", 0)), 0)
        show_stats = bool(arguments.get("show_stats", False))

        kb = KnowledgeBase()

        # Detect if any filters were provided
        has_filters = any([
            tags, categories, reading_status, collection, source
        ])

        # Default behavior: no filters and no explicit show_stats
        # Show stats + recent 10 papers
        if not has_filters and not show_stats:
            show_stats = True
            limit = 10

        # Fetch papers
        papers = await kb.list_papers(
            tags=tags,
            categories=categories,
            reading_status=reading_status,
            collection=collection,
            source=source,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )

        # Format paper summaries
        paper_summaries = [_format_paper_summary(p) for p in papers]

        # Build response
        response: Dict[str, Any] = {
            "total": len(paper_summaries),
            "papers": paper_summaries,
        }

        # Include stats if requested
        if show_stats:
            stats = await kb.get_stats()
            response["stats"] = stats

            collections = await kb.list_collections()
            response["collections"] = collections

        logger.info(
            f"KB list completed: {len(paper_summaries)} papers returned"
            f"{', with stats' if show_stats else ''}"
        )

        return [
            types.TextContent(
                type="text", text=json.dumps(response, indent=2)
            )
        ]

    except Exception as e:
        logger.error(f"KB list error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
