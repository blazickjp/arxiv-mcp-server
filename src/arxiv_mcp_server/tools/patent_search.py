"""Patent search and paper-patent cross-referencing via Lens.org.

Searches patents by claim text, scholarly works by title, and optionally
cross-references them (e.g., find patents citing top scholarly results).
Requires a free Lens.org API token set via LENS_API_TOKEN env var.
"""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.lens_client import LensClient

logger = logging.getLogger("arxiv-mcp-server")

patent_search_tool = types.Tool(
    name="patent_search",
    description=(
        "Search patents and scholarly works via Lens.org. Supports patent search "
        "(by claim text), scholarly search (by title), or both. Optionally "
        "cross-references results: for scholarly hits, finds citing patents; "
        "for patent hits, identifies cited papers. Requires free LENS_API_TOKEN "
        "(register at lens.org). Example: query='retrieval augmented generation', "
        "search_type='both', cross_reference=true"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query (e.g., 'retrieval augmented generation', "
                    "'document parsing neural network')."
                ),
            },
            "search_type": {
                "type": "string",
                "default": "both",
                "enum": ["patents", "scholarly", "both"],
                "description": (
                    "What to search: 'patents' (patent claims), "
                    "'scholarly' (academic works), or 'both' (default)."
                ),
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "maximum": 25,
                "description": "Max results per search type (default: 10, max: 25).",
            },
            "date_from": {
                "type": "string",
                "description": (
                    "Filter scholarly results from this date (YYYY-MM-DD). "
                    "Optional."
                ),
            },
            "cross_reference": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, cross-reference results: find patents citing "
                    "top scholarly works (by DOI). Adds latency. Default: false."
                ),
            },
        },
        "required": ["query"],
    },
    annotations=types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)


async def handle_patent_search(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle a patent_search tool request.

    Args:
        arguments: Tool input matching the ``patent_search`` schema.

    Returns:
        List with a single ``TextContent`` containing JSON results.
    """
    try:
        client = LensClient()

        # Check for API token
        if not client.has_token():
            return [
                types.TextContent(
                    type="text",
                    text=(
                        "Error: LENS_API_TOKEN environment variable is not set.\n\n"
                        "To use patent search, you need a free Lens.org API token:\n"
                        "1. Register at https://www.lens.org/lens/user/subscriptions\n"
                        "2. Request a free Scholarly or Patent API token\n"
                        "3. Set the LENS_API_TOKEN environment variable\n\n"
                        "Example: export LENS_API_TOKEN='your-token-here'"
                    ),
                )
            ]

        query = arguments["query"]
        search_type = arguments.get("search_type", "both")
        max_results = min(arguments.get("max_results", 10), 25)
        date_from = arguments.get("date_from")
        cross_reference = arguments.get("cross_reference", False)

        logger.info(
            "Patent search — query=%r, type=%s, max=%d, cross_ref=%s",
            query,
            search_type,
            max_results,
            cross_reference,
        )

        result: Dict[str, Any] = {"query": query, "search_type": search_type}

        # Search scholarly works
        if search_type in ("scholarly", "both"):
            try:
                scholarly = await client.search_scholarly(
                    query,
                    limit=max_results,
                    date_from=date_from,
                )
                result["scholarly"] = scholarly
            except Exception as exc:
                logger.warning("Lens scholarly search failed: %s", exc)
                result["scholarly"] = []
                result["scholarly_error"] = str(exc)

        # Search patents
        if search_type in ("patents", "both"):
            try:
                patents = await client.search_patents(
                    query,
                    limit=max_results,
                )
                result["patents"] = patents
            except Exception as exc:
                logger.warning("Lens patent search failed: %s", exc)
                result["patents"] = []
                result["patents_error"] = str(exc)

        # Cross-reference: find patents citing top scholarly results
        if cross_reference and result.get("scholarly"):
            cross_refs: List[Dict[str, Any]] = []
            # Only cross-reference top 5 scholarly results to limit API calls
            for work in result["scholarly"][:5]:
                doi = work.get("doi")
                if not doi:
                    continue
                try:
                    citing_patents = await client.find_patents_citing_paper(doi)
                    if citing_patents:
                        cross_refs.append({
                            "scholarly_doi": doi,
                            "scholarly_title": work.get("title", ""),
                            "citing_patents": citing_patents,
                        })
                except Exception as exc:
                    logger.warning(
                        "Cross-reference failed for DOI %s: %s", doi, exc
                    )
            result["cross_references"] = cross_refs

        output = json.dumps(result, indent=2, default=str)
        return [types.TextContent(type="text", text=output)]

    except Exception as exc:
        logger.error("Unexpected error in patent_search: %s", exc)
        return [
            types.TextContent(type="text", text=f"Error: {exc}")
        ]
