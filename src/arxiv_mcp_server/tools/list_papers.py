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
    filesystem_arxiv_stem,
    is_valid_arxiv_id,
    logical_arxiv_id_from_stem,
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
        "Returns id, title, authors, published, and arxiv_version/versioned_id "
        "from local metadata — no live re-fetch. "
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
                    "(id, title, authors, published, arxiv_version, versioned_id)."
                ),
            }
        },
        "required": [],
        "additionalProperties": False,
    },
)


def _raw_stored_stems(storage_path: Optional[Path] = None) -> list[str]:
    """Return every on-disk ``.md`` stem that looks like an arXiv ID.

    Filenames use a flat stem (legacy ``/`` stored as ``__``); returned values
    are logical arXiv IDs with the slash restored.
    """
    storage = Path(storage_path or settings.STORAGE_PATH)
    if not storage.exists():
        return []
    stems: list[str] = []
    for p in storage.iterdir():
        if not (p.is_file() and p.suffix == ".md"):
            continue
        logical = logical_arxiv_id_from_stem(p.stem)
        if is_valid_arxiv_id(logical):
            stems.append(logical)
    return stems


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
        Path(storage_path) / f"{filesystem_arxiv_stem(stem)}{METADATA_SUFFIX}"
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


def cached_storage_labels(
    paper_id: str, storage_path: Optional[Path] = None
) -> list[str]:
    """Return display labels for locally cached stems of this paper.

    Used when a requested version is missing so callers can name what *is*
    on disk (bare key with sidecar version, and/or legacy versioned stems).
    """
    requested = normalize_arxiv_id(paper_id) if paper_id else ""
    if not requested or not is_valid_arxiv_id(requested):
        return []
    bare = bare_arxiv_id(requested)
    group = _stems_for_bare_id(bare, storage_path=storage_path)
    labels: list[str] = []
    for stem in sorted(group, key=lambda s: (arxiv_version_number(s), s)):
        if stem == bare:
            ver = _sidecar_arxiv_version(stem, storage_path)
            labels.append(f"{bare} ({ver})" if ver else bare)
        else:
            labels.append(stem)
    return labels


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
    path = (
        Path(settings.STORAGE_PATH)
        / f"{filesystem_arxiv_stem(paper_id)}{METADATA_SUFFIX}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
    destination.parent.mkdir(parents=True, exist_ok=True)
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
    md_path = Path(settings.STORAGE_PATH) / f"{filesystem_arxiv_stem(paper_id)}.md"
    try:
        for line in md_path.read_text(encoding="utf-8").splitlines():
            text = line.strip().lstrip("#").strip()
            if text:
                return text
    except OSError:
        return None
    return None


def _version_fields_for_stem(
    stem: str, data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Return arxiv_version / versioned_id for a stored stem when known."""
    bare = bare_arxiv_id(stem)
    version = None
    if isinstance(data, dict):
        raw = data.get("arxiv_version")
        if isinstance(raw, str) and raw.strip():
            normalized = raw.strip().lower()
            if not normalized.startswith("v"):
                normalized = f"v{normalized}"
            if normalized[1:].isdigit():
                version = normalized
    if version is None:
        version = _sidecar_arxiv_version(stem) or arxiv_version_suffix(stem)
    fields: Dict[str, Any] = {"arxiv_version": version}
    if version:
        fields["versioned_id"] = f"{bare}{version}"
    else:
        fields["versioned_id"] = None
    return fields


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
                payload = {
                    "id": bare,
                    "title": data.get("title") or None,
                    "authors": [str(author) for author in authors],
                    "published": data.get("published") or None,
                }
                payload.update(_version_fields_for_stem(stem, data))
                return payload
    payload = {
        "id": bare,
        "title": _title_from_markdown(stem),
        "authors": [],
        "published": None,
    }
    payload.update(_version_fields_for_stem(stem))
    return payload


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
