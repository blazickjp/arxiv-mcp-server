"""List functionality for the arXiv MCP server."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.types import ToolAnnotations

from ..config import Settings
from .arxiv_ids import (
    arxiv_version_number,
    arxiv_version_suffix,
    bare_arxiv_id,
    is_valid_arxiv_id,
    normalize_arxiv_id,
)

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


def _raw_stored_stems(storage_path: Optional[Path] = None) -> list[str]:
    """Return every on-disk ``.md`` stem that looks like an arXiv ID."""
    storage = Path(storage_path or settings.STORAGE_PATH)
    if not storage.exists():
        return []
    return [
        p.stem
        for p in storage.iterdir()
        if p.is_file() and p.suffix == ".md" and is_valid_arxiv_id(p.stem)
    ]


def _stems_for_bare_id(
    bare_id: str,
    stems: Optional[List[str]] = None,
    storage_path: Optional[Path] = None,
) -> list[str]:
    """Return stored stems that share *bare_id* (including the bare stem)."""
    pool = stems if stems is not None else _raw_stored_stems(storage_path)
    return [stem for stem in pool if bare_arxiv_id(stem) == bare_id]


def preferred_stored_stem(candidates: List[str]) -> Optional[str]:
    """Pick the canonical on-disk stem for one paper.

    Prefer the bare-ID file (new storage model). Otherwise keep the highest
    versioned legacy key.
    """
    if not candidates:
        return None
    bare = bare_arxiv_id(candidates[0])
    if bare in candidates:
        return bare
    return max(candidates, key=arxiv_version_number)


def _sidecar_arxiv_version(
    stem: str, storage_path: Optional[Path] = None
) -> Optional[str]:
    """Read ``arxiv_version`` from a stem's sidecar, if present."""
    path = (
        Path(storage_path) / f"{stem}{METADATA_SUFFIX}"
        if storage_path is not None
        else paper_metadata_path(stem)
    )
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("arxiv_version")
    if isinstance(version, str) and version.strip():
        normalized = version.strip().lower()
        if not normalized.startswith("v"):
            normalized = f"v{normalized}"
        return normalized if normalized[1:].isdigit() else None
    return arxiv_version_suffix(stem)


def resolve_stored_stem(
    paper_id: str, storage_path: Optional[Path] = None
) -> Optional[str]:
    """Map a requested paper ID to an on-disk markdown stem.

    Bare IDs resolve to the bare storage key when present, otherwise to the
    highest stored versioned legacy key. Versioned IDs match an exact legacy
    stem, or a bare file whose sidecar records the same version (or has no
    version metadata — treated as readable content for that paper).

    Pass *storage_path* when the caller uses a different Settings instance
    than this module (tests often patch per-module settings).
    """
    requested = normalize_arxiv_id(paper_id) if paper_id else ""
    if not requested or not is_valid_arxiv_id(requested):
        return None

    stems = _raw_stored_stems(storage_path)
    if requested in stems:
        return requested

    bare = bare_arxiv_id(requested)
    group = _stems_for_bare_id(bare, stems, storage_path)
    if not group:
        return None

    req_ver = arxiv_version_suffix(requested)
    if req_ver is None:
        return preferred_stored_stem(group)

    if requested in group:
        return requested

    if bare in group:
        stored_ver = _sidecar_arxiv_version(bare, storage_path)
        if stored_ver is None or stored_ver == req_ver:
            return bare

    for stem in group:
        if arxiv_version_suffix(stem) == req_ver:
            return stem
    return None


def list_papers() -> list[str]:
    """List stored paper IDs, deduped to one bare ID per paper.

    Returns an empty list if the storage directory does not exist yet or
    contains no .md files. Only plain files with the .md suffix are
    considered; sub-directories and other file types are silently ignored.
    Legacy versioned filenames are folded into their bare ID so the same
    paper is never listed twice.
    """
    stems = _raw_stored_stems()
    return sorted({bare_arxiv_id(stem) for stem in stems})


def paper_metadata_path(paper_id: str) -> Path:
    """Return the sidecar path for locally stored paper metadata."""
    return Path(settings.STORAGE_PATH) / f"{paper_id}{METADATA_SUFFIX}"


def save_paper_metadata(
    paper_id: str,
    *,
    title: Optional[str] = None,
    authors: Optional[List[str]] = None,
    published: Optional[str] = None,
    extractor_version: Optional[int] = None,
    arxiv_version: Optional[str] = None,
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
    if extractor_version is not None:
        payload["extractor_version"] = extractor_version
    if arxiv_version:
        normalized = arxiv_version.strip().lower()
        if normalized and not normalized.startswith("v"):
            normalized = f"v{normalized}"
        if normalized and normalized[1:].isdigit():
            payload["arxiv_version"] = normalized
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
    bare = bare_arxiv_id(paper_id)
    stem = resolve_stored_stem(paper_id) or paper_id
    path = paper_metadata_path(stem)
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
                    "id": bare,
                    "title": data.get("title") or None,
                    "authors": [str(author) for author in authors],
                    "published": data.get("published") or None,
                }
    return {
        "id": bare,
        "title": _title_from_markdown(stem),
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
