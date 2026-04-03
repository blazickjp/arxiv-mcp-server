"""List functionality for the arXiv MCP server."""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import mcp.types as types
from ..config import Settings

settings = Settings()

list_tool = types.Tool(
    name="list_papers",
    description=(
        "List all papers that have been downloaded and stored locally via download_paper. "
        "Returns arXiv IDs only — use read_paper to access content. "
        "Returns an empty list if no papers have been downloaded yet. "
        "Workflow: search_papers -> download_paper -> list_papers -> read_paper."
    ),
    inputSchema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)


def list_papers() -> list[str]:
    """List all stored paper IDs.

    Returns an empty list if the storage directory does not exist yet or
    contains no .md files.  Only plain files with the .md suffix are
    considered; sub-directories and other file types are silently ignored.
    """
    storage = Path(settings.STORAGE_PATH)
    if not storage.exists():
        return []
    return [p.stem for p in storage.iterdir() if p.is_file() and p.suffix == ".md"]


async def handle_list_papers(
    arguments: Optional[Dict[str, Any]] = None,
) -> List[types.TextContent]:
    """Handle requests to list all stored papers."""
    try:
        papers = list_papers()

        # Short-circuit: nothing stored yet — avoid an empty arXiv API call
        if not papers:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {"total_papers": 0, "papers": []}, indent=2
                    ),
                )
            ]

        response_data = {
            "total_papers": len(papers),
            "papers": papers,
        }

        return [
            types.TextContent(type="text", text=json.dumps(response_data, indent=2))
        ]

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
