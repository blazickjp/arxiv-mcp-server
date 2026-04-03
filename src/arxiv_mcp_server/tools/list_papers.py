"""List functionality for the arXiv MCP server."""

import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
import mcp.types as types
from mcp.types import ToolAnnotations
from ..config import Settings

settings = Settings()

# Matches both new-style (YYMM.NNNNN) and old-style (cat/YYMMNNN) arXiv IDs,
# with optional version suffix (v1, v2, …).
_ARXIV_ID_RE = re.compile(
    r"^(\d{4}\.\d{4,5}(v\d+)?"  # new-style: 2404.18922 or 2404.18922v3
    r"|[a-z\-]+(/[a-z\-]+)?/\d{7}(v\d+)?)$",  # old-style: hep-ph/9901234
    re.IGNORECASE,
)


def is_valid_arxiv_id(stem: str) -> bool:
    """Return True if *stem* looks like a valid arXiv paper ID."""
    return bool(_ARXIV_ID_RE.match(stem))


list_tool = types.Tool(
    name="list_papers",
    annotations=ToolAnnotations(readOnlyHint=True),
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
    return [
        p.stem
        for p in storage.iterdir()
        if p.is_file() and p.suffix == ".md" and is_valid_arxiv_id(p.stem)
    ]


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
                    text=json.dumps({"total_papers": 0, "papers": []}, indent=2),
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
