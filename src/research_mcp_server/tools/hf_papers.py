"""MCP tool for trending AI papers from Hugging Face + linked models/datasets."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.hf_client import HuggingFaceClient

logger = logging.getLogger("research-mcp-server")


hf_trending_tool = types.Tool(
    name="hf_trending_papers",
    description="""Get trending AI papers from Hugging Face, or search papers by keyword. Optionally fetch linked HF models and datasets for each paper.

Use without query to see today's trending papers ranked by community upvotes. Use with query to search HF papers. Use date parameter to browse a specific day's trending papers.

Examples: {} (today's trending) | {"query": "vision transformers"} | {"date": "2026-03-25", "include_models": true}""",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search papers by keyword. If omitted, returns today's trending papers.",
            },
            "date": {
                "type": "string",
                "description": "Specific date (YYYY-MM-DD) for daily papers. Only used when query is omitted.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum number of papers to return. Default: 15.",
            },
            "include_models": {
                "type": "boolean",
                "description": "For each paper, also fetch linked HF models. Default: false.",
            },
            "include_datasets": {
                "type": "boolean",
                "description": "For each paper, also fetch linked HF datasets. Default: false.",
            },
        },
        "required": [],
    },
)


async def handle_hf_trending(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle the hf_trending_papers tool invocation.

    Args:
        arguments: Tool arguments from MCP.

    Returns:
        List containing a single TextContent with JSON results.
    """
    query = arguments.get("query")
    date = arguments.get("date")
    max_results = min(arguments.get("max_results", 15), 50)
    include_models = arguments.get("include_models", False)
    include_datasets = arguments.get("include_datasets", False)

    client = HuggingFaceClient()

    try:
        # Fetch papers
        if query:
            papers = await client.search_papers(query, limit=max_results)
        else:
            papers = await client.get_daily_papers(date=date)

        papers = papers[:max_results]

        if not papers:
            msg = "No papers found"
            if query:
                msg += f" for query '{query}'"
            elif date:
                msg += f" for date {date}"
            msg += ". Try broadening your search or using a different date."
            return [types.TextContent(type="text", text=msg)]

        # Enrich with linked models/datasets if requested
        if include_models or include_datasets:
            for paper in papers:
                arxiv_id = paper.get("arxiv_id", "")
                if not arxiv_id:
                    continue

                try:
                    detail = await client.get_paper(arxiv_id)
                except (ValueError, Exception) as exc:
                    logger.debug(f"Could not fetch detail for {arxiv_id}: {exc}")
                    continue

                if include_models:
                    raw_models = detail.get("models", [])
                    paper["linked_models"] = [
                        {
                            "id": m.get("id", m) if isinstance(m, dict) else m,
                            "url": f"https://huggingface.co/{m.get('id', m) if isinstance(m, dict) else m}",
                        }
                        for m in raw_models[:5]
                    ]

                if include_datasets:
                    raw_datasets = detail.get("datasets", [])
                    paper["linked_datasets"] = [
                        {
                            "id": d.get("id", d) if isinstance(d, dict) else d,
                            "url": f"https://huggingface.co/datasets/{d.get('id', d) if isinstance(d, dict) else d}",
                        }
                        for d in raw_datasets[:5]
                    ]

        # Build response
        result = {
            "source": "huggingface",
            "query": query,
            "date": date,
            "total_results": len(papers),
            "papers": papers,
        }

        return [
            types.TextContent(
                type="text",
                text=json.dumps(result, indent=2, default=str),
            )
        ]

    except Exception as exc:
        logger.error(f"HF trending papers error: {exc}")
        return [
            types.TextContent(
                type="text",
                text=f"Error fetching Hugging Face papers: {exc}",
            )
        ]
