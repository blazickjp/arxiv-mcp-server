"""Community tool — unified access to Dev.to and Lobsters developer content."""

import json
import logging
import asyncio
from typing import Any, Dict, List

import mcp.types as types

from ..clients.devto_client import DevtoClient
from ..clients.lobsters_client import LobstersClient

logger = logging.getLogger("research-mcp-server")

community_tool = types.Tool(
    name="community",
    description=(
        "Search and browse developer community content from Dev.to and Lobsters. "
        "Higher signal-to-noise than social media for technical content.\n"
        "Actions:\n"
        "- 'search': Search Dev.to articles by keyword/tag.\n"
        "- 'trending': Get trending articles from Dev.to, Lobsters, or both.\n"
        "- 'by_tag': Get Lobsters stories by tag (ai, python, rust, etc.)."
    ),
    inputSchema={
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "trending", "by_tag"],
                "description": "The operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query (for 'search' action).",
            },
            "tag": {
                "type": "string",
                "description": "Tag to filter by (for 'search' and 'by_tag'). E.g., 'python', 'ai', 'webdev', 'rust'.",
            },
            "source_filter": {
                "type": "string",
                "enum": ["devto", "lobsters", "both"],
                "description": "Which source(s) to query (for 'trending'). Default: both.",
            },
            "time_range": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
                "description": "Time range for trending (for Dev.to 'trending'). Default: week.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "description": "Maximum results per source. Default: 15.",
            },
        },
    },
)


async def handle_community(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Dispatch community tool calls."""
    action = arguments.get("action")
    if not action:
        return [types.TextContent(type="text", text="Error: 'action' is required.")]

    max_results = arguments.get("max_results", 15)
    devto = DevtoClient()
    lobsters = LobstersClient()

    try:
        if action == "search":
            query = arguments.get("query")
            tag = arguments.get("tag")
            if not query and not tag:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: 'query' or 'tag' required for search.",
                    )
                ]
            results = await devto.search(
                query=query or "", tag=tag, max_results=max_results
            )
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "total": len(results),
                            "source": "devto",
                            "articles": results,
                        },
                        indent=2,
                    ),
                )
            ]

        elif action == "trending":
            source_filter = arguments.get("source_filter", "both")
            time_range = arguments.get("time_range", "week")

            results: Dict[str, Any] = {}
            tasks = []

            if source_filter in ("devto", "both"):
                tasks.append(
                    (
                        "devto",
                        devto.trending(
                            time_range=time_range, max_results=max_results
                        ),
                    )
                )
            if source_filter in ("lobsters", "both"):
                tasks.append(
                    ("lobsters", lobsters.hottest(max_results=max_results))
                )

            for source_name, coro in tasks:
                try:
                    results[source_name] = await coro
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch {source_name} trending: {e}"
                    )
                    results[source_name] = []

            total = sum(len(v) for v in results.values())
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({"total": total, **results}, indent=2),
                )
            ]

        elif action == "by_tag":
            tag = arguments.get("tag")
            if not tag:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: 'tag' is required for by_tag.",
                    )
                ]
            results = await lobsters.by_tag(tag=tag, max_results=max_results)
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "total": len(results),
                            "source": "lobsters",
                            "tag": tag,
                            "stories": results,
                        },
                        indent=2,
                    ),
                )
            ]

        else:
            return [
                types.TextContent(
                    type="text", text=f"Error: Unknown action '{action}'."
                )
            ]

    except Exception as e:
        logger.error(f"Community tool error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
