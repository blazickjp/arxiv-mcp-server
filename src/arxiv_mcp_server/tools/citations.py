"""Citation graph explorer using the Semantic Scholar API."""

import json
import logging
from typing import Dict, Any, List, Optional

import mcp.types as types

from ..clients.s2_client import S2Client
from ..utils.rate_limiter import s2_limiter

logger = logging.getLogger("arxiv-mcp-server")


citation_graph_tool = types.Tool(
    name="arxiv_citation_graph",
    description="""Explore the citation graph of an arXiv paper using the Semantic Scholar API.

Returns citations (papers that cite this paper), references (papers this paper cites),
or both directions. Supports up to depth=2 for recursive traversal.

WARNING: depth=2 makes many API calls and can be slow. Use max_per_level to limit.

EXAMPLES:
- Get all citation info for a paper: paper_id="2401.12345", direction="both"
- Find who cited a paper: paper_id="2401.12345", direction="citations"
- See what a paper builds on: paper_id="2401.12345", direction="references"
- Deep graph (slow): paper_id="2401.12345", direction="citations", depth=2, max_per_level=10""",
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "arXiv paper ID (e.g., '2401.12345'). Do not include version suffix.",
            },
            "direction": {
                "type": "string",
                "enum": ["citations", "references", "both"],
                "description": "Which direction to traverse: 'citations' (papers citing this one), 'references' (papers this one cites), or 'both'. Default: 'both'.",
            },
            "depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2,
                "description": "How many levels deep to traverse. Default: 1. WARNING: depth=2 makes many API calls.",
            },
            "max_per_level": {
                "type": "integer",
                "minimum": 5,
                "maximum": 100,
                "description": "Maximum number of papers to fetch per level. Default: 20.",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Semantic Scholar fields to include (e.g., ['title', 'year', 'citationCount']). Uses sensible defaults if omitted.",
            },
        },
        "required": ["paper_id"],
    },
)


async def _fetch_level(
    client: S2Client,
    paper_id: str,
    direction: str,
    limit: int,
    fields: Optional[str],
) -> Dict[str, Any]:
    """Fetch one level of citations or references for a paper.

    Args:
        client: S2Client instance.
        paper_id: S2 paper ID or arXiv ID.
        direction: 'citations' or 'references'.
        limit: Max papers to fetch.
        fields: S2 field string.

    Returns:
        Dict with 'papers' list and 'direction'.
    """
    if direction == "citations":
        papers = await client.get_citations(paper_id, limit=limit, fields=fields)
    else:
        papers = await client.get_references(paper_id, limit=limit, fields=fields)
    return {"direction": direction, "papers": papers}


async def handle_citation_graph(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle citation graph exploration requests.

    Fetches citation and/or reference data for a paper, optionally
    traversing one additional level deep.
    """
    try:
        paper_id = arguments["paper_id"]
        direction = arguments.get("direction", "both")
        depth = arguments.get("depth", 1)
        max_per_level = arguments.get("max_per_level", 20)
        fields_list: Optional[List[str]] = arguments.get("fields")

        # Clamp depth
        depth = max(1, min(depth, 2))
        max_per_level = max(5, min(max_per_level, 100))

        # Build fields string
        fields_str: Optional[str] = None
        if fields_list:
            fields_str = ",".join(fields_list)

        client = S2Client()

        # Fetch root paper metadata
        try:
            root_paper = await client.get_paper(paper_id)
        except ValueError as e:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": "paper_not_found",
                            "message": str(e),
                            "suggestion": (
                                "Ensure the arXiv ID is correct and does not "
                                "include a version suffix (e.g., use '2401.12345' "
                                "not '2401.12345v2')."
                            ),
                        },
                        indent=2,
                    ),
                )
            ]

        result: Dict[str, Any] = {
            "root_paper": {
                "paperId": root_paper.get("paperId"),
                "title": root_paper.get("title"),
                "authors": [
                    a.get("name", "") for a in root_paper.get("authors", [])
                ],
                "year": root_paper.get("year"),
                "venue": root_paper.get("venue"),
                "abstract": root_paper.get("abstract"),
                "citationCount": root_paper.get("citationCount"),
                "influentialCitationCount": root_paper.get(
                    "influentialCitationCount"
                ),
                "referenceCount": root_paper.get("referenceCount"),
                "fieldsOfStudy": root_paper.get("fieldsOfStudy"),
                "isOpenAccess": root_paper.get("isOpenAccess"),
                "publicationDate": root_paper.get("publicationDate"),
            },
            "stats": {
                "total_citations": root_paper.get("citationCount", 0),
                "influential_citations": root_paper.get(
                    "influentialCitationCount", 0
                ),
                "reference_count": root_paper.get("referenceCount", 0),
            },
        }

        # Determine which directions to fetch
        directions: List[str] = []
        if direction in ("citations", "both"):
            directions.append("citations")
        if direction in ("references", "both"):
            directions.append("references")

        # Fetch level 1
        for d in directions:
            level_data = await _fetch_level(
                client, paper_id, d, max_per_level, fields_str
            )
            result[d] = level_data["papers"]

            # Fetch level 2 if requested
            if depth == 2 and level_data["papers"]:
                level2_key = f"{d}_depth2"
                result[level2_key] = {}

                for child_paper in level_data["papers"]:
                    child_s2_id = child_paper.get("paperId")
                    if not child_s2_id:
                        continue

                    await s2_limiter.wait()
                    try:
                        child_level = await _fetch_level(
                            client,
                            child_s2_id,
                            d,
                            max(5, max_per_level // 2),
                            fields_str,
                        )
                        child_title = child_paper.get("title", child_s2_id)
                        result[level2_key][child_title] = child_level["papers"]
                    except Exception as e:
                        logger.warning(
                            f"Failed to fetch depth-2 {d} for "
                            f"{child_s2_id}: {e}"
                        )
                        continue

        return [
            types.TextContent(type="text", text=json.dumps(result, indent=2))
        ]

    except ValueError as e:
        logger.error(f"Citation graph error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    except Exception as e:
        logger.error(f"Unexpected citation graph error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
