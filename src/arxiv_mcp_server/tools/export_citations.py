"""Export BibTeX citations for arXiv papers using authoritative arXiv metadata.

Scoped per maintainer request in issue #41: BibTeX only (RIS/CSL-JSON are follow-up
work), one ``export_citations`` tool over one or more validated arXiv IDs, metadata
taken from the arXiv API (never model-generated), version suffixes preserved where the
caller supplies them, deterministic citation keys, and no heavy formatting dependency.
"""

import json
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional

import httpx
import mcp.types as types
from mcp.types import ToolAnnotations

from .list_papers import is_valid_arxiv_id
from .search import ARXIV_API_URL, _parse_arxiv_atom_response, _rate_limited_get

logger = logging.getLogger("arxiv-mcp-server")

# Bound the response so a single call cannot fan out without limit.
MAX_IDS = 50

_VERSION_SUFFIX = re.compile(r"v\d+$", re.IGNORECASE)

# BibTeX special characters, escaped in field values. Backslash is handled via a
# sentinel so the backslashes we introduce here are not re-escaped.
_BIBTEX_REPLACEMENTS = [
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]


def _bibtex_escape(text: str) -> str:
    """Escape characters that are special in BibTeX field values."""
    if not text:
        return ""
    out = text.replace("\\", "\x00")
    for char, repl in _BIBTEX_REPLACEMENTS:
        out = out.replace(char, repl)
    return out.replace("\x00", r"\textbackslash{}")


def _ascii_token(value: str) -> str:
    """Fold *value* to lowercase ASCII alphanumerics (deterministic, accent-safe)."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def _base_id(paper_id: str) -> str:
    """Strip a trailing version suffix, keeping the bare arXiv identifier."""
    return _VERSION_SUFFIX.sub("", paper_id)


def _year_of(published: str) -> str:
    """Extract a four-digit year from an arXiv ``published`` timestamp."""
    return published[:4] if published[:4].isdigit() else ""


def _citation_key(authors: List[str], year: str, title: str) -> str:
    """Deterministic key: first-author surname + year + first title word.

    Falls back to whatever parts are available so a key is always produced.
    """
    surname = ""
    if authors and authors[0].split():
        surname = _ascii_token(authors[0].split()[-1])
    title_word = ""
    for word in title.split():
        token = _ascii_token(word)
        if token:
            title_word = token
            break
    key = f"{surname}{year}{title_word}"
    return key or "arxiv"


def _render_entry(key: str, paper: Dict[str, Any], requested_id: str) -> str:
    """Render a single ``@misc`` BibTeX entry from authoritative arXiv metadata."""
    fields: List[tuple] = []
    title = paper.get("title", "")
    if title:
        fields.append(("title", _bibtex_escape(title)))
    authors = paper.get("authors") or []
    if authors:
        fields.append(("author", " and ".join(_bibtex_escape(a) for a in authors)))
    year = _year_of(paper.get("published", ""))
    if year:
        fields.append(("year", year))
    # Preserve the version suffix the caller supplied; otherwise the bare ID.
    fields.append(("eprint", requested_id))
    fields.append(("archivePrefix", "arXiv"))
    categories = paper.get("categories") or []
    if categories:
        fields.append(("primaryClass", categories[0]))
    fields.append(("url", f"https://arxiv.org/abs/{requested_id}"))

    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@misc{{{key},\n{body}\n}}"


async def _fetch_metadata(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch authoritative metadata for *ids* in one arXiv API request.

    Returns a mapping of bare arXiv ID -> parsed metadata dict.
    """
    url = f"{ARXIV_API_URL}?id_list={','.join(ids)}&max_results={len(ids)}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await _rate_limited_get(client, url)
    papers = _parse_arxiv_atom_response(response.text)
    return {paper["id"]: paper for paper in papers if paper.get("id")}


def _error(message: str) -> List[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps({"status": "error", "message": message}))]


export_citations_tool = types.Tool(
    name="export_citations",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Export BibTeX citations for one or more arXiv papers using authoritative arXiv "
        "metadata (title, authors, year, primary category), never model-generated fields. "
        "Version suffixes (e.g. '2401.12345v2') are preserved and citation keys are "
        "deterministic. Returns the rendered BibTeX plus per-paper status/error. "
        "BibTeX only; RIS/CSL-JSON are not yet supported."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": MAX_IDS,
                "description": (
                    "arXiv IDs, new-style ('2401.12345', optionally versioned "
                    "'2401.12345v2') or legacy ('hep-ph/9901234')."
                ),
            }
        },
        "required": ["paper_ids"],
        "additionalProperties": False,
    },
)


async def handle_export_citations(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Build BibTeX for the requested arXiv IDs with per-paper status reporting."""
    try:
        raw_ids = arguments.get("paper_ids")
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list) or not raw_ids:
            return _error("paper_ids must be a non-empty list of arXiv IDs")
        if len(raw_ids) > MAX_IDS:
            return _error(f"too many IDs: {len(raw_ids)} (max {MAX_IDS})")

        valid_ids = [
            pid.strip()
            for pid in raw_ids
            if isinstance(pid, str) and is_valid_arxiv_id(pid.strip())
        ]
        metadata = await _fetch_metadata(valid_ids) if valid_ids else {}

        results: List[Dict[str, Any]] = []
        used_keys: set = set()
        for pid in raw_ids:
            candidate = pid.strip() if isinstance(pid, str) else ""
            if not candidate or not is_valid_arxiv_id(candidate):
                results.append(
                    {"paper_id": pid, "status": "error", "error": "invalid arXiv ID format"}
                )
                continue
            paper = metadata.get(_base_id(candidate))
            if not paper:
                results.append(
                    {"paper_id": candidate, "status": "error", "error": "not found on arXiv"}
                )
                continue
            base_key = _citation_key(
                paper.get("authors") or [],
                _year_of(paper.get("published", "")),
                paper.get("title", ""),
            )
            key, suffix = base_key, 0
            while key in used_keys:  # deterministic disambiguation within the batch
                suffix += 1
                key = f"{base_key}{chr(ord('a') + suffix - 1)}"
            used_keys.add(key)
            results.append(
                {
                    "paper_id": candidate,
                    "status": "success",
                    "key": key,
                    "bibtex": _render_entry(key, paper, candidate),
                }
            )

        succeeded = [r for r in results if r["status"] == "success"]
        failed = [r for r in results if r["status"] != "success"]
        overall = "success" if not failed else ("partial" if succeeded else "error")
        payload = {
            "status": overall,
            "format": "bibtex",
            "bibtex": "\n\n".join(r["bibtex"] for r in succeeded),
            "results": results,
            "count": {
                "requested": len(raw_ids),
                "succeeded": len(succeeded),
                "failed": len(failed),
            },
        }
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

    except RuntimeError as exc:  # rate limit / timeout surfaced by _rate_limited_get
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - report, don't crash the server
        logger.error(f"export_citations error: {exc}")
        return _error(str(exc))
