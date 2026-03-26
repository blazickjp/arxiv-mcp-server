"""MCP tool for CS venue lookup and enrichment via DBLP."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.dblp_client import DBLPClient

logger = logging.getLogger("research-mcp-server")


# Well-known venue tiers for enrichment (non-exhaustive, CS-focused)
_VENUE_TIERS: dict[str, str] = {
    # Top ML/AI conferences
    "neurips": "A*",
    "nips": "A*",
    "icml": "A*",
    "iclr": "A*",
    "aaai": "A*",
    "ijcai": "A*",
    # Top systems/architecture
    "osdi": "A*",
    "sosp": "A*",
    "isca": "A*",
    "micro": "A*",
    # Top PL/SE
    "pldi": "A*",
    "popl": "A*",
    "icse": "A*",
    "fse": "A*",
    # Top DB
    "sigmod": "A*",
    "vldb": "A*",
    # Top networks/security
    "sigcomm": "A*",
    "nsdi": "A*",
    "ccs": "A*",
    "s&p": "A*",
    "usenix security": "A*",
    # Top HCI/graphics
    "chi": "A*",
    "siggraph": "A*",
    # Top NLP/CV
    "acl": "A*",
    "emnlp": "A*",
    "naacl": "A",
    "cvpr": "A*",
    "iccv": "A*",
    "eccv": "A*",
    # Top theory
    "stoc": "A*",
    "focs": "A*",
    # A-tier conferences
    "aistats": "A",
    "uai": "A",
    "coling": "A",
    "wacv": "A",
    "kdd": "A*",
    "www": "A*",
    "acm mm": "A",
    "colt": "A*",
    # Journals
    "jmlr": "A*",
    "tmlr": "A",
    "tpami": "A*",
    "nature": "A*",
    "science": "A*",
    "tacl": "A*",
}


def _get_venue_tier(venue_name: str) -> str:
    """Look up the approximate tier of a venue.

    Args:
        venue_name: Venue name or acronym.

    Returns:
        Tier string (e.g., "A*", "A") or "unknown".
    """
    if not venue_name:
        return "unknown"
    venue_lower = venue_name.lower().strip()
    for key, tier in _VENUE_TIERS.items():
        if key in venue_lower:
            return tier
    return "unknown"


venue_lookup_tool = types.Tool(
    name="venue_lookup",
    description="""Search the DBLP computer science bibliography for publications, authors, or venues (conferences/journals).

Returns enriched results with venue tier info where possible. Use to find papers by topic, discover an author's publication list, or look up conference/journal details.

Examples:
  query="attention is all you need", type="publication" | query="Geoffrey Hinton", type="author" | query="NeurIPS", type="venue"
""",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — paper title/keywords, author name, or venue name.",
            },
            "type": {
                "type": "string",
                "enum": ["publication", "author", "venue"],
                "description": "Type of search: 'publication', 'author', or 'venue'. Default: 'publication'.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum number of results. Default: 10.",
            },
        },
        "required": ["query"],
    },
)


async def handle_venue_lookup(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle venue lookup requests by routing to the appropriate DBLP search.

    Args:
        arguments: Tool input arguments.

    Returns:
        List containing a single TextContent with JSON results.
    """
    try:
        query = arguments["query"]
        search_type = arguments.get("type", "publication")
        max_results = min(max(arguments.get("max_results", 10), 1), 50)

        client = DBLPClient()

        if search_type == "publication":
            results = await client.search_publications(
                query=query, max_results=max_results
            )
            # Enrich with venue tier
            for r in results:
                r["venue_tier"] = _get_venue_tier(r.get("venue", ""))

            response = {
                "type": "publication",
                "query": query,
                "count": len(results),
                "results": results,
            }

        elif search_type == "author":
            results = await client.search_authors(
                query=query, max_results=max_results
            )
            response = {
                "type": "author",
                "query": query,
                "count": len(results),
                "results": results,
            }

        elif search_type == "venue":
            results = await client.search_venues(
                query=query, max_results=max_results
            )
            # Enrich with tier info
            for r in results:
                venue_name = r.get("venue", "") or r.get("acronym", "")
                r["tier"] = _get_venue_tier(venue_name)

            response = {
                "type": "venue",
                "query": query,
                "count": len(results),
                "results": results,
            }

        else:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": "invalid_type",
                            "message": f"Unknown type '{search_type}'. Use 'publication', 'author', or 'venue'.",
                        },
                        indent=2,
                    ),
                )
            ]

        return [
            types.TextContent(type="text", text=json.dumps(response, indent=2))
        ]

    except Exception as e:
        logger.error(f"Venue lookup error: {e}")
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {"error": "venue_lookup_error", "message": str(e)},
                    indent=2,
                ),
            )
        ]
