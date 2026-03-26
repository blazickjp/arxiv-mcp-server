"""Advanced structured query builder for the arXiv MCP server.

Translates natural parameters (title, author, abstract, etc.) into
arXiv's query syntax using the existing ``build_query`` / ``advanced_search``
helpers from ``..clients.arxiv_client``.
"""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.arxiv_client import advanced_search

logger = logging.getLogger("arxiv-mcp-server")

advanced_query_tool = types.Tool(
    name="arxiv_advanced_query",
    description=(
        "Structured query builder for arXiv. Accepts individual search fields "
        "(title, author, abstract, etc.) and combines them into an optimised "
        "arXiv query. Prefer this over search_papers when you know the specific "
        "fields you want to search."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Search in paper titles (ti: prefix).",
            },
            "author": {
                "type": "string",
                "description": "Search by author name (au: prefix).",
            },
            "abstract": {
                "type": "string",
                "description": "Search in abstracts (abs: prefix).",
            },
            "all_fields": {
                "type": "string",
                "description": "Search across all fields (all: prefix).",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 10,
                "description": (
                    "arXiv category codes to filter by (OR'd together). "
                    "E.g. ['cs.AI', 'cs.LG']. Maximum 10."
                ),
            },
            "date_from": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format.",
            },
            "date_to": {
                "type": "string",
                "description": "End date in YYYY-MM-DD format.",
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum number of results to return (default: 10).",
            },
            "sort_by": {
                "type": "string",
                "default": "relevance",
                "enum": ["relevance", "lastUpdatedDate", "submittedDate"],
                "description": "Sort criterion (default: relevance).",
            },
            "sort_order": {
                "type": "string",
                "default": "descending",
                "enum": ["ascending", "descending"],
                "description": "Sort direction (default: descending).",
            },
            "exclude_terms": {
                "type": "string",
                "description": "Terms to exclude from results (ANDNOT).",
            },
        },
        "required": [],
    },
    annotations=types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)


async def handle_advanced_query(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle an advanced structured query request against arXiv.

    Args:
        arguments: Tool input matching the ``arxiv_advanced_query`` schema.

    Returns:
        List with a single ``TextContent`` containing JSON results or an error.
    """
    try:
        title = arguments.get("title")
        author = arguments.get("author")
        abstract = arguments.get("abstract")
        all_fields = arguments.get("all_fields")
        categories = arguments.get("categories")
        date_from = arguments.get("date_from")
        date_to = arguments.get("date_to")
        max_results = min(max(int(arguments.get("max_results", 10)), 1), 50)
        sort_by = arguments.get("sort_by", "relevance")
        sort_order = arguments.get("sort_order", "descending")
        exclude_terms = arguments.get("exclude_terms")

        # Validate that at least one search criterion was provided
        has_field = any([title, author, abstract, all_fields])
        has_date = any([date_from, date_to])
        if not has_field and not categories and not has_date:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        "Error: At least one search field (title, author, "
                        "abstract, all_fields), a category, or a date range "
                        "must be provided."
                    ),
                )
            ]

        logger.info(
            "Advanced query — title=%s author=%s abstract=%s categories=%s "
            "date_from=%s date_to=%s max_results=%d sort_by=%s",
            title,
            author,
            abstract,
            categories,
            date_from,
            date_to,
            max_results,
            sort_by,
        )

        results = await advanced_search(
            title=title,
            author=author,
            abstract=abstract,
            all_fields=all_fields,
            categories=categories,
            exclude_terms=exclude_terms,
            date_from=date_from,
            date_to=date_to,
            max_results=max_results,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        response_data = {
            "total_results": len(results),
            "papers": results,
        }

        return [
            types.TextContent(
                type="text",
                text=json.dumps(response_data, indent=2),
            )
        ]

    except ValueError as exc:
        logger.warning("Advanced query validation error: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {exc}")]
    except Exception as exc:
        logger.error("Unexpected error in advanced query: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {exc}")]
