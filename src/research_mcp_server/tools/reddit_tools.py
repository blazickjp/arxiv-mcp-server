"""Reddit tool — search dev subreddits, trending posts, discussion threads."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.reddit_client import RedditClient, DEV_SUBREDDITS

logger = logging.getLogger("research-mcp-server")

reddit_tool = types.Tool(
    name="reddit",
    description=(
        "Search and browse Reddit developer communities. Great for practitioner sentiment, "
        "tool recommendations, and honest discussions. Works without auth (public JSON) or "
        "with REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET for better rate limits.\n"
        "Actions:\n"
        "- 'search': Search posts across dev subreddits or a specific subreddit.\n"
        "- 'trending': Get hot/top posts from curated dev subreddits.\n"
        "- 'discussion': Get a post's top comments for sentiment/opinions."
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
                "description": "Search query (for 'search'). E.g., 'best Python web framework 2026', 'Drizzle vs Prisma'.",
            },
            "subreddit": {
                "type": "string",
                "description": "Specific subreddit (for 'search' and 'discussion'). E.g., 'MachineLearning', 'devops'. Omit to search across all.",
            },
            "subreddits": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"Subreddits to browse (for 'trending'). Default: top 5 from: {', '.join(DEV_SUBREDDITS[:8])}...",
            },
            "sort": {
                "type": "string",
                "enum": ["relevance", "hot", "top", "new", "comments", "best"],
                "description": "Sort order. For 'search': relevance/hot/top/new/comments. For 'trending': hot/top/new. For 'discussion': best/top/new.",
            },
            "time_filter": {
                "type": "string",
                "enum": ["hour", "day", "week", "month", "year", "all"],
                "description": "Time filter (for 'search' sort=top/relevance). Default: month.",
            },
            "post_id": {
                "type": "string",
                "description": "Reddit post ID (for 'discussion'). Get from search/trending results.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "description": "Maximum results. Default: 15.",
            },
        },
    },
)


async def handle_reddit(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Dispatch Reddit tool calls."""
    action = arguments.get("action")
    if not action:
        return [types.TextContent(type="text", text="Error: 'action' is required.")]

    client = RedditClient()
    max_results = arguments.get("max_results", 15)

    try:
        if action == "search":
            query = arguments.get("query")
            if not query:
                return [types.TextContent(type="text", text="Error: 'query' is required for search.")]
            results = await client.search(
                query=query,
                subreddit=arguments.get("subreddit"),
                sort=arguments.get("sort", "relevance"),
                time_filter=arguments.get("time_filter", "month"),
                max_results=max_results,
            )
            return [types.TextContent(type="text", text=json.dumps({"total": len(results), "posts": results}, indent=2))]

        elif action == "trending":
            results = await client.trending(
                subreddits=arguments.get("subreddits"),
                sort=arguments.get("sort", "hot"),
                max_results=max_results,
            )
            return [types.TextContent(type="text", text=json.dumps({"total": len(results), "posts": results}, indent=2))]

        elif action == "discussion":
            post_id = arguments.get("post_id")
            if not post_id:
                return [types.TextContent(type="text", text="Error: 'post_id' is required for discussion.")]
            result = await client.get_discussion(
                post_id=post_id,
                subreddit=arguments.get("subreddit"),
                sort=arguments.get("sort", "best"),
                max_comments=max_results,
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [types.TextContent(type="text", text=f"Error: Unknown action '{action}'.")]

    except Exception as e:
        logger.error(f"Reddit tool error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
