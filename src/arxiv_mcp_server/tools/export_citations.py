"""Export BibTeX citations for arXiv papers using authoritative arXiv metadata.

Scoped per maintainer request in issue #41: one ``export_citations`` tool over one or
more validated arXiv IDs, metadata
taken from the arXiv API (never model-generated), version suffixes preserved where the
caller supplies them, deterministic citation keys, and no heavy formatting dependency.

BibTeX shipped first (#135); RIS and CSL-JSON follow here behind a ``format`` argument,
sharing the same fetch, validation and per-paper status contract.
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

# Rendering formats. "bibtex" stays the default so existing callers are unaffected.
FORMATS = ("bibtex", "ris", "csl-json")
DEFAULT_FORMAT = "bibtex"
# Payload key that carries the rendered citations, per format.
_PAYLOAD_KEY = {"bibtex": "bibtex", "ris": "ris", "csl-json": "csl"}

# Every arXiv paper carries a DataCite DOI derived from its identifier; verified to
# resolve for both new-style ("2401.12345") and legacy ("hep-ph/9901234") IDs.
ARXIV_DOI_PREFIX = "10.48550/arXiv."

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
    folded = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
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


def _alpha_suffix(index: int) -> str:
    """Return a base-26 alphabetic suffix: 1 -> 'a', 26 -> 'z', 27 -> 'aa'.

    Plain ``chr(ord('a') + n)`` walks past 'z' into '{', '|', '}', which are not
    legal in a BibTeX citation key and break the entry structurally.
    """
    out = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        out = chr(ord("a") + remainder) + out
    return out


def _unique_key(base_key: str, used_keys: set) -> str:
    """Return *base_key*, suffixed alphabetically until it is unused."""
    key = base_key
    index = 0
    while key in used_keys:
        index += 1
        key = f"{base_key}{_alpha_suffix(index)}"
    return key


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


def _arxiv_doi(requested_id: str) -> str:
    """DataCite DOI for an arXiv paper, derived from the bare identifier."""
    return f"{ARXIV_DOI_PREFIX}{_base_id(requested_id)}"


def _one_line(text: str) -> str:
    """Collapse whitespace: RIS is a line-oriented format, tags cannot wrap."""
    return " ".join(text.split())


# Corporate authors must not be split into given/family — CSL carries them literally.
_CORPORATE_MARKERS = ("collaboration", "collaborations", "consortium", "team")


def _split_name(author: str) -> tuple:
    """Split a display name into (family, given) for CSL.

    arXiv gives names as free text in "Given Middle Family" order, so the last token is
    the family name. Single-token names and corporate authors ("LIGO Scientific
    Collaboration") return ("", "") — the caller then emits a CSL ``literal`` name,
    which is how such authors are represented rather than as person names.
    """
    parts = author.split()
    if len(parts) < 2:
        return ("", "")
    if any(part.lower().strip(".,") in _CORPORATE_MARKERS for part in parts):
        return ("", "")
    return (parts[-1], " ".join(parts[:-1]))


def _render_ris(key: str, paper: Dict[str, Any], requested_id: str) -> str:
    """Render one RIS record.

    ``TY  - GEN`` (generic) is used rather than ``JOUR``: an arXiv entry is a preprint,
    not a journal article, and RIS has no preprint type. Two spaces before the hyphen
    are part of the format, not padding.
    """
    lines: List[str] = ["TY  - GEN"]
    for author in paper.get("authors") or []:
        lines.append(f"AU  - {_one_line(author)}")
    title = paper.get("title", "")
    if title:
        lines.append(f"TI  - {_one_line(title)}")
    year = _year_of(paper.get("published", ""))
    if year:
        lines.append(f"PY  - {year}")
    published = paper.get("published", "")
    if len(published) >= 10 and published[4] == "-" and published[7] == "-":
        lines.append(f"DA  - {published[:4]}/{published[5:7]}/{published[8:10]}")
    for category in paper.get("categories") or []:
        lines.append(f"KW  - {category}")
    lines.append("PB  - arXiv")
    lines.append(f"DO  - {_arxiv_doi(requested_id)}")
    lines.append(f"UR  - https://arxiv.org/abs/{requested_id}")
    lines.append(f"ID  - {key}")
    lines.append("ER  - ")
    return "\n".join(lines)


def _render_csl(key: str, paper: Dict[str, Any], requested_id: str) -> Dict[str, Any]:
    """Render one CSL-JSON item.

    CSL has no ``preprint`` item type — the schema's 45 types offer ``article``,
    ``article-journal``, ``manuscript`` and ``report``. ``article`` plus
    ``publisher: arXiv`` and ``genre: preprint`` is the closest faithful mapping.
    """
    item: Dict[str, Any] = {"id": key, "type": "article", "genre": "preprint"}
    title = paper.get("title", "")
    if title:
        item["title"] = _one_line(title)
    authors = []
    for author in paper.get("authors") or []:
        family, given = _split_name(author)
        authors.append(
            {"family": family, "given": given} if family else {"literal": author}
        )
    if authors:
        item["author"] = authors
    published = paper.get("published", "")
    year = _year_of(published)
    if year:
        date_parts: List[int] = [int(year)]
        if len(published) >= 10 and published[4] == "-" and published[7] == "-":
            date_parts += [int(published[5:7]), int(published[8:10])]
        item["issued"] = {"date-parts": [date_parts]}
    categories = paper.get("categories") or []
    if categories:
        item["categories"] = list(categories)
    item["publisher"] = "arXiv"
    item["number"] = requested_id
    item["DOI"] = _arxiv_doi(requested_id)
    item["URL"] = f"https://arxiv.org/abs/{requested_id}"
    return item


def _render(fmt: str, key: str, paper: Dict[str, Any], requested_id: str):
    """Dispatch to the renderer for *fmt*."""
    if fmt == "ris":
        return _render_ris(key, paper, requested_id)
    if fmt == "csl-json":
        return _render_csl(key, paper, requested_id)
    return _render_entry(key, paper, requested_id)


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
    return [
        types.TextContent(
            type="text", text=json.dumps({"status": "error", "message": message})
        )
    ]


export_citations_tool = types.Tool(
    name="export_citations",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Export citations for one or more arXiv papers using authoritative arXiv "
        "metadata (title, authors, year, primary category), never model-generated fields. "
        "Formats: BibTeX (default), RIS, CSL-JSON. Version suffixes (e.g. '2401.12345v2') "
        "are preserved, citation keys are deterministic, and each paper carries its own "
        "status/error alongside the rendered citation."
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
            },
            "format": {
                "type": "string",
                "enum": list(FORMATS),
                "default": DEFAULT_FORMAT,
                "description": (
                    "Citation format: 'bibtex' (default), 'ris', or 'csl-json'. "
                    "CSL-JSON is returned as structured items, the others as text."
                ),
            },
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

        fmt = arguments.get("format", DEFAULT_FORMAT)
        if not isinstance(fmt, str) or fmt.lower() not in FORMATS:
            return _error(
                f"unsupported format: {fmt!r} (expected one of {', '.join(FORMATS)})"
            )
        fmt = fmt.lower()
        payload_key = _PAYLOAD_KEY[fmt]

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
                    {
                        "paper_id": pid,
                        "status": "error",
                        "error": "invalid arXiv ID format",
                    }
                )
                continue
            paper = metadata.get(_base_id(candidate))
            if not paper:
                results.append(
                    {
                        "paper_id": candidate,
                        "status": "error",
                        "error": "not found on arXiv",
                    }
                )
                continue
            base_key = _citation_key(
                paper.get("authors") or [],
                _year_of(paper.get("published", "")),
                paper.get("title", ""),
            )
            key = _unique_key(base_key, used_keys)
            used_keys.add(key)
            results.append(
                {
                    "paper_id": candidate,
                    "status": "success",
                    "key": key,
                    payload_key: _render(fmt, key, paper, candidate),
                }
            )

        succeeded = [r for r in results if r["status"] == "success"]
        failed = [r for r in results if r["status"] != "success"]
        # "error" when nothing resolved is intentional: server.call_tool turns a
        # top-level {"status": "error"} payload into an MCP isError result, which
        # matches the not-found convention asserted in
        # tests/test_mcp_tool_error_semantics.py. The JSON body — including
        # per-paper diagnostics — is preserved in the error content.
        overall = "success" if not failed else ("partial" if succeeded else "error")
        rendered = [r[payload_key] for r in succeeded]
        combined = rendered if fmt == "csl-json" else "\n\n".join(rendered)
        payload = {
            "status": overall,
            "format": fmt,
            payload_key: combined,
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
