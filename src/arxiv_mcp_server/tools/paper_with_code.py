"""Papers With Code search tool -- find papers with code implementations and benchmarks."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.pwc_client import PapersWithCodeClient

logger = logging.getLogger("arxiv-mcp-server")


pwc_search_tool = types.Tool(
    name="papers_with_code_search",
    description="""Search Papers With Code for papers with open-source implementations and benchmark results. Uniquely connects papers to runnable GitHub repos, SOTA benchmark rankings, methods, and datasets.

Use when: user wants code for a paper, wants to find implementations, wants SOTA benchmark tables, or wants to know which frameworks/repos exist for a technique.

Paper IDs are URL slugs (e.g., "attention-is-all-you-need"). You can search by title, topic, or arXiv ID.

Examples: query="attention is all you need" | query="diffusion models" include_benchmarks=true | query="2401.12345" include_repos=true""",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (paper title, topic, or arXiv ID).",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "description": "Maximum number of papers to return. Default: 10.",
            },
            "include_repos": {
                "type": "boolean",
                "description": "Fetch GitHub repositories for each paper. Default: true.",
            },
            "include_benchmarks": {
                "type": "boolean",
                "description": "Fetch SOTA benchmark results for each paper. Default: false. Slower -- makes additional API calls per paper.",
            },
        },
        "required": ["query"],
    },
)


async def handle_pwc_search(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle Papers With Code search requests.

    Searches for papers, then optionally enriches each result with
    repository and benchmark data.
    """
    try:
        query = arguments["query"]
        max_results = min(arguments.get("max_results", 10), 25)
        include_repos = arguments.get("include_repos", True)
        include_benchmarks = arguments.get("include_benchmarks", False)

        client = PapersWithCodeClient()

        # Search for papers
        papers = await client.search(query, items_per_page=max_results)

        if not papers:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "query": query,
                            "total_results": 0,
                            "papers": [],
                            "suggestion": (
                                "No results found. Try broader search terms, "
                                "the full paper title, or an arXiv ID."
                            ),
                        },
                        indent=2,
                    ),
                )
            ]

        # Truncate to max_results
        papers = papers[:max_results]

        # Enrich each paper with repos and/or benchmarks
        enriched_papers: List[Dict[str, Any]] = []
        for paper in papers:
            enriched: Dict[str, Any] = {**paper}
            paper_id = paper.get("id", "")

            if not paper_id:
                enriched_papers.append(enriched)
                continue

            if include_repos:
                try:
                    repos = await client.get_repositories(paper_id)
                    enriched["repositories"] = repos
                except Exception as e:
                    logger.warning(f"Failed to fetch repos for {paper_id}: {e}")
                    enriched["repositories"] = []

            if include_benchmarks:
                try:
                    results = await client.get_results(paper_id)
                    enriched["benchmarks"] = results
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch benchmarks for {paper_id}: {e}"
                    )
                    enriched["benchmarks"] = []

            enriched_papers.append(enriched)

        response = {
            "query": query,
            "total_results": len(enriched_papers),
            "papers": enriched_papers,
        }

        return [
            types.TextContent(type="text", text=json.dumps(response, indent=2))
        ]

    except ValueError as e:
        logger.error(f"PWC search error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    except Exception as e:
        logger.error(f"Unexpected PWC search error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
