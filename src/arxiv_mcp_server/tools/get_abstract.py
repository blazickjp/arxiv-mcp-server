"""Get paper abstract functionality for the arXiv MCP server."""

import arxiv
import json
import logging
from typing import Dict, Any, List
import mcp.types as types

logger = logging.getLogger("arxiv-mcp-server")

abstract_tool = types.Tool(
    name="get_abstract",
    description="""Get the abstract and metadata of a paper by its arXiv ID, WITHOUT downloading the full paper.

USE THIS TOOL FIRST to assess whether a paper is relevant before using download_paper + read_paper.
This saves significant tokens by avoiding full paper downloads when only the abstract is needed.

Returns: title, authors, abstract, categories, published date, and PDF URL.""",
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "The arXiv ID of the paper (e.g., '2401.12345')",
            }
        },
        "required": ["paper_id"],
    },
)


async def handle_get_abstract(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle requests to get a paper's abstract without downloading."""
    try:
        paper_id = arguments["paper_id"]
        client = arxiv.Client()
        search = arxiv.Search(id_list=[paper_id])
        results = list(client.results(search))

        if not results:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": f"Paper {paper_id} not found on arXiv",
                        }
                    ),
                )
            ]

        paper = results[0]
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "paper_id": paper_id,
                        "title": paper.title,
                        "authors": [a.name for a in paper.authors],
                        "abstract": paper.summary,
                        "categories": paper.categories,
                        "published": paper.published.isoformat(),
                        "pdf_url": paper.pdf_url,
                    },
                    indent=2,
                ),
            )
        ]

    except Exception as e:
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "message": f"Error: {str(e)}"}
                ),
            )
        ]
