"""List functionality for the arXiv MCP server."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.types import ToolAnnotations

from ..config import Settings
from .arxiv_ids import is_valid_arxiv_id

settings = Settings()
logger = logging.getLogger("arxiv-mcp-server")

METADATA_SUFFIX = ".meta.json"


list_tool = types.Tool(
    name="list_papers",
    annotations=ToolAnnotations(readOnlyHint=True),
    description=(
        "List all papers that have been downloaded and stored locally via download_paper. "
        "Returns id, title, authors, and published from local metadata — no live re-fetch. "
        "Set compact=true to return arXiv IDs only. "
        "Returns an empty list if no papers have been downloaded yet. "
        "Workflow: search_papers -> download_paper -> list_papers -> read_paper."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "compact": {
                "type": "boolean",
                "description": (
                    "If true, return arXiv IDs only. Default is full local metadata "
                    "(id, title, authors, published)."
                ),
            }
        },
        "required": [],
        "additionalProperties": False,
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


def paper_metadata_path(paper_id: str) -> Path:
    """Return the sidecar path for locally stored paper metadata."""
    return Path(settings.STORAGE_PATH) / f"{paper_id}{METADATA_SUFFIX}"


def save_paper_metadata(
    paper_id: str,
    *,
    title: Optional[str] = None,
    authors: Optional[List[str]] = None,
    published: Optional[str] = None,
    path: Optional[Path] = None,
) -> None:
    """Persist lightweight paper metadata next to the downloaded markdown."""
    destination = path or paper_metadata_path(paper_id)
    payload = {
        "id": paper_id,
        "title": title or None,
        "authors": list(authors or []),
        "published": published or None,
    }
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _title_from_markdown(paper_id: str) -> Optional[str]:
    """Best-effort title from the first non-empty markdown line (local only)."""
    md_path = Path(settings.STORAGE_PATH) / f"{paper_id}.md"
    try:
        for line in md_path.read_text(encoding="utf-8").splitlines():
            text = line.strip().lstrip("#").strip()
            if text:
                return text
    except OSError:
        return None
    return None


def load_paper_metadata(paper_id: str) -> Dict[str, Any]:
    """Load local metadata for a stored paper without hitting the network."""
    path = paper_metadata_path(paper_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable metadata for %s: %s", paper_id, exc)
        else:
            if isinstance(data, dict):
                authors = data.get("authors") or []
                if not isinstance(authors, list):
                    authors = []
                return {
                    "id": paper_id,
                    "title": data.get("title") or None,
                    "authors": [str(author) for author in authors],
                    "published": data.get("published") or None,
                }
    return {
        "id": paper_id,
        "title": _title_from_markdown(paper_id),
        "authors": [],
        "published": None,
    }


async def handle_list_papers(
    arguments: Optional[Dict[str, Any]] = None,
) -> List[types.TextContent]:
    """Handle requests to list all stored papers."""
    try:
        paper_ids = list_papers()
        compact = bool((arguments or {}).get("compact"))

        if not paper_ids:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({"total_papers": 0, "papers": []}, indent=2),
                )
            ]

        papers: List[Any] = (
            paper_ids
            if compact
            else [load_paper_metadata(paper_id) for paper_id in paper_ids]
        )
        response_data = {
            "total_papers": len(paper_ids),
            "papers": papers,
        }

        return [
            types.TextContent(type="text", text=json.dumps(response_data, indent=2))
        ]

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
