"""Hacker News tool — search stories, get trending, read discussions."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.hn_client import HNClient

logger = logging.getLogger("research-mcp-server")

hn_tool = types.Tool(
    name="hn",
    description=(
        "Search and browse Hacker News for developer discussions, tech news, and community reactions. "
        "Actions:\n"
        "- 'search': Search HN stories or comments by keyword. Filter by time range.\n"
        "- 'trending': Get current front page stories (what devs are discussing right now).\n"
        "- 'discussion': Get a story's top comments (community reaction/sentiment)."
    ),
    inputSchema={
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "trending", "discussion"],
                "description": "The operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query (for 'search' action). E.g., 'LangChain vs LangGraph', 'Rust web framework'.",
            },
            "search_type": {
                "type": "string",
                "enum": ["story", "comment"],
                "description": "What to search (for 'search' action): 'story' (titles/links) or 'comment' (discussion text). Default: story.",
            },
            "sort": {
                "type": "string",
                "enum": ["relevance", "date"],
                "description": "Sort order (for 'search' action): 'relevance' or 'date' (newest first). Default: relevance.",
            },
            "time_range": {
                "type": "string",
                "enum": ["24h", "week", "month", "year"],
                "description": "Time filter (for 'search' action). Default: no filter (all time).",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum results to return. Default: 20.",
            },
            "story_id": {
                "type": "string",
                "description": "HN story ID (for 'discussion' action). Get from search/trending results.",
            },
        },
    },
)


async def handle_hn(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Dispatch HN tool calls."""
    action = arguments.get("action")
    if not action:
        return [types.TextContent(type="text", text="Error: 'action' is required.")]

    client = HNClient()

    try:
        if action == "search":
            query = arguments.get("query")
            if not query:
                return [types.TextContent(type="text", text="Error: 'query' is required for search.")]
            results = await client.search(
                query=query,
                search_type=arguments.get("search_type", "story"),
                sort=arguments.get("sort", "relevance"),
                time_range=arguments.get("time_range"),
                max_results=arguments.get("max_results", 20),
            )
            return [types.TextContent(type="text", text=json.dumps({"total": len(results), "stories": results}, indent=2))]

        elif action == "trending":
            results = await client.trending(limit=arguments.get("max_results", 20))
            return [types.TextContent(type="text", text=json.dumps({"total": len(results), "stories": results}, indent=2))]

        elif action == "discussion":
            story_id = arguments.get("story_id")
            if not story_id:
                return [types.TextContent(type="text", text="Error: 'story_id' is required for discussion.")]
            result = await client.get_discussion(
                story_id=story_id,
                max_comments=arguments.get("max_results", 20),
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [types.TextContent(type="text", text=f"Error: Unknown action '{action}'.")]

    except Exception as e:
        logger.error(f"HN tool error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
