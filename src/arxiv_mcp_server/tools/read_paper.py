"""Read functionality for the arXiv MCP server."""

import json
from pathlib import Path
from typing import Dict, Any, List
import mcp.types as types
from ..config import Settings

settings = Settings()

read_tool = types.Tool(
    name="read_paper",
    description="Read the full markdown content of a downloaded paper. Prerequisite: the paper must already be downloaded via download_paper (use list_papers to check). Returns the complete paper text converted from PDF. Example: paper_id=\"2401.12345\"",
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "The arXiv ID of the paper to read",
            }
        },
        "required": ["paper_id"],
    },
)


def list_papers() -> list[str]:
    """List all stored paper IDs."""
    return [p.stem for p in Path(settings.STORAGE_PATH).glob("*.md")]


async def handle_read_paper(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle requests to read a paper's content."""
    try:
        paper_ids = list_papers()
        paper_id = arguments["paper_id"]
        # Check if paper exists
        if paper_id not in paper_ids:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": f"Paper {paper_id} not found in storage. You may need to download it first using download_paper.",
                        }
                    ),
                )
            ]

        # Get paper content
        content = Path(settings.STORAGE_PATH, f"{paper_id}.md").read_text(
            encoding="utf-8"
        )

        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "paper_id": paper_id,
                        "content": content,
                    }
                ),
            )
        ]

    except Exception as e:
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "message": f"Error reading paper: {str(e)}",
                    }
                ),
            )
        ]
