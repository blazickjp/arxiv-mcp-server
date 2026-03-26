"""GitHub tool — search repos, get stats, compare, trending, releases."""

import json
import logging
from typing import Any, Dict, List

import httpx
import mcp.types as types

from ..clients.github_client import GitHubClient

logger = logging.getLogger("research-mcp-server")

github_tool = types.Tool(
    name="github",
    description=(
        "Search and analyze GitHub repositories. Understand OSS adoption, compare tools, "
        "track releases. Optional GITHUB_TOKEN env var for higher rate limits.\n"
        "Actions:\n"
        "- 'search': Search repos by keyword, topic, or language. Sorted by stars.\n"
        "- 'repo': Get detailed info for a specific repo (stars, forks, contributors, activity).\n"
        "- 'compare': Compare 2+ repos side-by-side.\n"
        "- 'trending': Get trending repos (recently created, high stars).\n"
        "- 'releases': Get recent releases for a repo."
    ),
    inputSchema={
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "repo", "compare", "trending", "releases"],
                "description": "The operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query (for 'search'). Supports GitHub search qualifiers: 'language:python topic:llm', 'fastapi stars:>1000'.",
            },
            "owner_repo": {
                "type": "string",
                "description": "Repository in 'owner/repo' format (for 'repo' and 'releases'). E.g., 'langchain-ai/langchain'.",
            },
            "repos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of 'owner/repo' strings (for 'compare'). E.g., ['prisma/prisma', 'drizzle-team/drizzle-orm'].",
            },
            "language": {
                "type": "string",
                "description": "Filter by programming language (for 'trending' and 'search'). E.g., 'python', 'rust', 'typescript'.",
            },
            "since": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "description": "Time range for 'trending'. Default: weekly.",
            },
            "sort": {
                "type": "string",
                "enum": ["stars", "forks", "updated", "help-wanted-issues"],
                "description": "Sort order for 'search'. Default: stars.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "description": "Maximum results. Default: 20.",
            },
        },
    },
)


async def handle_github(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Dispatch GitHub tool calls."""
    action = arguments.get("action")
    if not action:
        return [types.TextContent(type="text", text="Error: 'action' is required.")]

    client = GitHubClient()
    max_results = arguments.get("max_results", 20)

    try:
        if action == "search":
            query = arguments.get("query")
            if not query:
                return [types.TextContent(type="text", text="Error: 'query' is required for search.")]
            language = arguments.get("language")
            if language and f"language:{language}" not in query:
                query += f" language:{language}"
            results = await client.search_repos(
                query=query,
                sort=arguments.get("sort", "stars"),
                max_results=max_results,
            )
            return [types.TextContent(type="text", text=json.dumps({"total": len(results), "repos": results}, indent=2))]

        elif action == "repo":
            owner_repo = arguments.get("owner_repo")
            if not owner_repo:
                return [types.TextContent(type="text", text="Error: 'owner_repo' is required (e.g., 'langchain-ai/langchain').")]
            result = await client.get_repo(owner_repo)
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif action == "compare":
            repos = arguments.get("repos")
            if not repos or len(repos) < 2:
                return [types.TextContent(type="text", text="Error: 'repos' array with 2+ items required.")]
            results = await client.compare_repos(repos)
            return [types.TextContent(type="text", text=json.dumps({"comparison": results}, indent=2))]

        elif action == "trending":
            results = await client.trending(
                language=arguments.get("language"),
                since=arguments.get("since", "weekly"),
                max_results=max_results,
            )
            return [types.TextContent(type="text", text=json.dumps({"total": len(results), "repos": results}, indent=2))]

        elif action == "releases":
            owner_repo = arguments.get("owner_repo")
            if not owner_repo:
                return [types.TextContent(type="text", text="Error: 'owner_repo' is required.")]
            results = await client.get_releases(owner_repo, max_results=min(max_results, 10))
            return [types.TextContent(type="text", text=json.dumps({"repo": owner_repo, "releases": results}, indent=2))]

        else:
            return [types.TextContent(type="text", text=f"Error: Unknown action '{action}'.")]

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            return [types.TextContent(type="text", text="Error: GitHub rate limit exceeded. Set GITHUB_TOKEN env var for higher limits (5000 req/hr).")]
        elif e.response.status_code == 404:
            return [types.TextContent(type="text", text=f"Error: Repository not found. Check the owner/repo format.")]
        return [types.TextContent(type="text", text=f"Error: GitHub API error {e.response.status_code}: {str(e)}")]
    except Exception as e:
        logger.error(f"GitHub tool error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
