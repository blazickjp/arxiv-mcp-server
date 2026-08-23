"""Outline, section, and bounded passage retrieval for downloaded markdown papers.

Section IDs use hierarchical counters (``1``, ``1.1``, ``1.1.1``), matching the
LaTeX outline tools. Missing heading levels insert ``0`` placeholders (e.g. an
``h3`` after an ``h1`` becomes ``1.0.1``). Duplicate titles remain distinct via
those counters. Papers with no recognized headings (ATX, arabic/IEEE-roman numbered, or
common bare arXiv section titles) expose a synthetic section ``1``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.types import ToolAnnotations

from ..config import Settings
from .arxiv_ids import (
    arxiv_version_suffix,
    bare_arxiv_id,
    normalize_arxiv_id,
    parse_arxiv_id,
)
from .content import add_content_payload, CONTENT_WARNING
from .list_papers import _sidecar_arxiv_version, resolve_stored_stem

settings = Settings()

MAX_PAPER_ID_CHARS = 40
MAX_SECTION_ID_CHARS = 200
MAX_QUERY_CHARS = 200
DEFAULT_MAX_SECTIONS = 100
MAX_RETURNED_SECTIONS = 200
DEFAULT_MAX_PASSAGES = 8
MAX_RETURNED_PASSAGES = 25
DEFAULT_PASSAGE_CHARS = 800
MAX_PASSAGE_CHARS = 2000
MAX_SECTION_COUNT = 2000

# Heading styles seen in arXiv markdown (ATX, numbered, bare HTML titles).
_ATX_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,5})[ \t]+(.+?)[ \t]*$")
# HTML→text often emits the section number alone ("3." / "3.1.") then the title.
_NUMBER_ONLY_RE = re.compile(r"^(\d+(?:\.\d+){0,5})\.[ \t]*$")
# IEEE/latexml HTML often splits roman section tags onto their own line:
# ``I`` / ``Introduction``, ``II-A`` / ``Related Work``. Longer numerals first.
_ROMAN_NUMERAL = (
    r"(?:XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X|" r"IX|VIII|VII|VI|V|IV|III|II|I)"
)
_ROMAN_MARKER_RE = re.compile(rf"^({_ROMAN_NUMERAL}(?:-[A-Z])*)$")
_ROMAN_INLINE_RE = re.compile(rf"^({_ROMAN_NUMERAL}(?:-[A-Z])*)[ \t]+(.+?)$")
_FENCE_RE = re.compile(r"^```")
_MAX_BARE_TITLE_CHARS = 80
# Stop collecting headings once References/Bibliography is seen (ref-line pollution).
_OUTLINE_TERMINATORS = frozenset({"reference", "references", "bibliography"})
# Top-level section indices stay small; years like 2023 USENIX… are common FPs.
_MAX_SECTION_INDEX = 99

# Common standalone section titles from arXiv HTML→md conversions.
_BARE_SECTION_TITLES = frozenset(
    {
        "abstract",
        "introduction",
        "background",
        "related work",
        "related works",
        "method",
        "methods",
        "methodology",
        "approach",
        "experiment",
        "experiments",
        "experimental setup",
        "experimental results",
        "result",
        "results",
        "discussion",
        "discussions",
        "conclusion",
        "conclusions",
        "future work",
        "limitations",
        "reference",
        "references",
        "appendix",
        "appendices",
        "acknowledgement",
        "acknowledgements",
        "acknowledgment",
        "acknowledgments",
        "bibliography",
        "notation",
        "preliminaries",
        "problem formulation",
        "problem statement",
    }
)


@dataclass(frozen=True)
class MdSection:
    """One markdown heading span with stable hierarchical ID."""

    section_id: str
    level: int
    title: str
    start: int
    end: int


def _error(message: str, paper_id: str | None = None) -> list[types.TextContent]:
    payload: dict[str, Any] = {"status": "error", "message": message}
    if paper_id:
        payload["paper_id"] = paper_id
    return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]


def _paper_id_property() -> dict[str, Any]:
    return {
        "type": "string",
        "maxLength": MAX_PAPER_ID_CHARS,
        "description": "Validated modern or legacy arXiv paper ID",
    }


def _page_properties() -> dict[str, Any]:
    return {
        "start": {
            "type": "integer",
            "minimum": 0,
            "description": "Zero-based character offset within this section",
        },
        "max_chars": {
            "type": "integer",
            "minimum": 1,
            "description": "Maximum section characters to return (default 12,000)",
        },
        "return_full_text": {
            "type": "boolean",
            "description": "If true, return the entire remaining section from start",
        },
    }


def _mask_fenced_code(content: str) -> str:
    """Space-out non-newline chars inside fenced code blocks."""
    chars = list(content)
    lines_meta: list[tuple[int, int, str]] = []
    offset = 0
    for line in content.splitlines(keepends=True):
        lines_meta.append((offset, offset + len(line), line))
        offset += len(line)

    in_fence = False
    for start, end, line in lines_meta:
        stripped = line.lstrip(" \t")
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            for i in range(start, end):
                if chars[i] not in "\r\n":
                    chars[i] = " "
    return "".join(chars)


def _normalize_heading_title(title: str) -> str:
    """Strip trailing section punctuation common in HTML→text titles."""
    cleaned = title.strip().rstrip(":.").strip()
    return cleaned or title.strip()


def _is_plausible_section_number(numbering: str) -> bool:
    """Reject years and other non-outline numbers (e.g. ``2023 USENIX…``)."""
    parts = numbering.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return False
    return all(1 <= int(part) <= _MAX_SECTION_INDEX for part in parts)


def _is_outline_terminator(title: str) -> bool:
    return _normalize_heading_title(title).casefold() in _OUTLINE_TERMINATORS


def _title_looks_like_heading(title: str, *, line_len: int) -> bool:
    """Shared capitalization / length / venue guards for numbered headings."""
    title = title.strip()
    if not title or line_len > _MAX_BARE_TITLE_CHARS:
        return False
    # Section titles are capitalized; reject mid-sentence prose joins.
    if not re.match(r"[A-Z]", title):
        return False
    # Citation venues often look like "USENIX ATC 23" after a year number.
    if re.search(r"\b(?:19|20)\d{2}\b", title):
        return False
    return True


def _numbered_heading(
    numbering: str, title: str, *, line_len: int
) -> tuple[int, str] | None:
    """Return (level, title) for a plausible numbered heading, else None."""
    if not _title_looks_like_heading(title, line_len=line_len):
        return None
    if not _is_plausible_section_number(numbering):
        return None
    level = min(6, numbering.count(".") + 1)
    return level, _normalize_heading_title(title.strip())


def _roman_heading(marker: str, title: str, *, line_len: int) -> tuple[int, str] | None:
    """Return (level, title) for an IEEE roman section marker, else None."""
    if _ROMAN_MARKER_RE.match(marker.strip()) is None:
        return None
    if not _title_looks_like_heading(title, line_len=line_len):
        return None
    level = min(6, 1 + marker.strip().count("-"))
    return level, _normalize_heading_title(title.strip())


def _match_heading_line(line: str) -> tuple[int, str] | None:
    """Return (level, title) for a heading line, or None if not a heading.

    Preference: ATX > arabic-numbered > IEEE roman > bare known section titles.
    Bare titles must be short standalone lines that exactly match a known
    section name (optional trailing period only — trailing colon is rejected
    so algorithm ``Result:`` / ``Data:`` lines are not outlines).
    """
    stripped = line.strip()
    if not stripped:
        return None

    atx = _ATX_HEADING_RE.match(stripped)
    if atx:
        title = atx.group(2).strip()
        if title:
            return len(atx.group(1)), title
        return None

    numbered = _NUMBERED_HEADING_RE.match(stripped)
    if numbered:
        matched = _numbered_heading(
            numbered.group(1), numbered.group(2), line_len=len(stripped)
        )
        if matched is not None:
            return matched

    roman_inline = _ROMAN_INLINE_RE.match(stripped)
    if roman_inline:
        matched = _roman_heading(
            roman_inline.group(1),
            roman_inline.group(2),
            line_len=len(stripped),
        )
        if matched is not None:
            return matched

    if len(stripped) <= _MAX_BARE_TITLE_CHARS:
        # Algorithm listings use ``Result:`` / ``Data:``; do not treat as sections.
        if stripped.endswith(":"):
            return None
        candidate = _normalize_heading_title(stripped)
        # Exact known title only — no extra tokens (tightens bare heuristics).
        if candidate.casefold() in _BARE_SECTION_TITLES:
            remainder = stripped
            if remainder.endswith("."):
                remainder = remainder[:-1]
            if remainder.strip().casefold() == candidate.casefold():
                return 1, candidate

    return None


def _logical_line(line: str) -> str:
    logical = line[:-1] if line.endswith("\n") else line
    if logical.endswith("\r"):
        logical = logical[:-1]
    return logical


def _match_split_numbered_heading(
    number_line: str, title_line: str
) -> tuple[int, str] | None:
    """Join HTML→text ``3.`` / ``Title`` or ``II-A`` / ``Title`` pairs."""
    stripped = number_line.strip()
    title_stripped = title_line.strip()
    if not title_stripped:
        return None
    # Do not join onto ATX / inline-numbered headings or another number marker.
    # Bare known titles (e.g. Method) *should* join with the preceding number.
    if _ATX_HEADING_RE.match(title_stripped):
        return None
    if _NUMBERED_HEADING_RE.match(title_stripped):
        return None
    if _NUMBER_ONLY_RE.match(title_stripped):
        return None
    if _ROMAN_MARKER_RE.match(title_stripped):
        return None
    if _ROMAN_INLINE_RE.match(title_stripped):
        return None

    line_len = len(stripped) + 1 + len(title_stripped)
    num_only = _NUMBER_ONLY_RE.match(stripped)
    if num_only is not None:
        return _numbered_heading(num_only.group(1), title_stripped, line_len=line_len)
    if _ROMAN_MARKER_RE.match(stripped):
        return _roman_heading(stripped, title_stripped, line_len=line_len)
    return None


def _is_bare_section_title_line(line: str) -> bool:
    """True when the line is only a known bare section title (not numbered)."""
    stripped = line.strip()
    if not stripped or stripped.endswith(":"):
        return False
    if _ATX_HEADING_RE.match(stripped):
        return False
    if _NUMBERED_HEADING_RE.match(stripped):
        return False
    if _ROMAN_INLINE_RE.match(stripped):
        return False
    candidate = _normalize_heading_title(stripped)
    if candidate.casefold() not in _BARE_SECTION_TITLES:
        return False
    remainder = stripped[:-1] if stripped.endswith(".") else stripped
    return remainder.strip().casefold() == candidate.casefold()


def _bare_title_has_section_body(
    lines_meta: list[tuple[int, str]], after_index: int
) -> bool:
    """Reject bare titles whose next line looks like a table cell, not a body.

    Real HTML→text sections are followed by prose, another heading, or a
    split number marker. Table column headers are followed by another short
    single-token cell (e.g. ``Method`` then ``HellaS``).
    """
    peek = after_index
    while peek < len(lines_meta):
        _, peek_line = lines_meta[peek]
        peek_logical = _logical_line(peek_line)
        if not peek_logical.strip():
            peek += 1
            continue
        if peek_logical.lstrip(" \t").startswith("```"):
            return True
        if _match_heading_line(peek_logical) is not None:
            return True
        stripped = peek_logical.strip()
        if _NUMBER_ONLY_RE.match(stripped) or _ROMAN_MARKER_RE.match(stripped):
            return True
        # Identifier-like neighbor (Model / Method / HellaS) ⇒ table chrome.
        # Sentence fragments like ``Body.`` keep the bare title.
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", stripped):
            return False
        return True
    return True


def parse_markdown_sections(content: str) -> list[MdSection]:
    """Parse ATX, numbered, and bare arXiv headings into hierarchical sections.

    Section body runs from the heading start through the character before the
    next heading of the same or higher level (lower or equal level number).
    Scanning stops after a References/Bibliography heading so bibliography
    lines (e.g. ``2023 USENIX ATC…``) are not treated as sections.
    """
    if len(content) == 0:
        return [MdSection("1", 1, "(document)", 0, 0)]

    masked = _mask_fenced_code(content)
    raw: list[tuple[int, str, str, int]] = []
    counters = [0, 0, 0, 0, 0, 0]

    lines_meta: list[tuple[int, str]] = []
    offset = 0
    for line in masked.splitlines(keepends=True):
        lines_meta.append((offset, line))
        offset += len(line)

    in_fence = False
    index = 0
    while index < len(lines_meta):
        if len(raw) >= MAX_SECTION_COUNT:
            break
        start_offset, line = lines_meta[index]
        stripped = line.lstrip(" \t")
        if stripped.startswith("```"):
            in_fence = not in_fence
            index += 1
            continue
        if in_fence:
            index += 1
            continue

        logical = _logical_line(line)
        matched = _match_heading_line(logical)
        consumed = 1

        if matched is None:
            # Peek across blank lines for a title after a lone section number.
            peek = index + 1
            while peek < len(lines_meta):
                _, peek_line = lines_meta[peek]
                peek_logical = _logical_line(peek_line)
                if not peek_logical.strip():
                    peek += 1
                    continue
                if peek_logical.lstrip(" \t").startswith("```"):
                    break
                matched = _match_split_numbered_heading(logical, peek_logical)
                if matched is not None:
                    consumed = peek - index + 1
                break

        if matched is not None:
            level, title = matched
            # Drop table-header false positives: bare ``Method`` between short
            # single-token cells (Model / Method / HellaS) is not a section.
            # Keep References/Bibliography even when the next line is ``[1]``.
            if (
                _is_bare_section_title_line(logical)
                and not _is_outline_terminator(title)
                and not _bare_title_has_section_body(lines_meta, index + consumed)
            ):
                index += 1
                continue
            counters[level - 1] += 1
            for counter_index in range(level, 6):
                counters[counter_index] = 0
            section_id = ".".join(str(value) for value in counters[:level])
            raw.append((level, section_id, title, start_offset))
            if _is_outline_terminator(title):
                break
        index += consumed

    if not raw:
        return [MdSection("1", 1, "(document)", 0, len(content))]

    sections: list[MdSection] = []
    for index, (level, section_id, title, start) in enumerate(raw):
        end = len(content)
        for next_level, _nid, _ntitle, next_start in raw[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        sections.append(MdSection(section_id, level, title, start, end))
    return sections


def _find_section(sections: list[MdSection], section_id: str) -> MdSection | None:
    needle = section_id.strip()
    if not needle:
        return None
    for section in sections:
        if section.section_id == needle:
            return section
    # Case-insensitive title fallback (exact title match after normalize).
    title_needle = re.sub(r"\s+", " ", needle).casefold()
    matches = [
        s for s in sections if re.sub(r"\s+", " ", s.title).casefold() == title_needle
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _section_for_offset(sections: list[MdSection], offset: int) -> MdSection | None:
    """Return the deepest section whose span contains offset."""
    best: MdSection | None = None
    for section in sections:
        if section.start <= offset < section.end:
            if best is None or section.level > best.level:
                best = section
    return best


def _load_paper(
    raw_id: Any,
) -> tuple[str, str | None, str | None, str] | list[types.TextContent]:
    """Return (bare_id, version, versioned_id, content) or an error TextContent list."""
    paper_id = parse_arxiv_id(raw_id) if isinstance(raw_id, str) else None
    if paper_id is None and isinstance(raw_id, str):
        paper_id = normalize_arxiv_id(raw_id)
    if (
        paper_id is None
        or not isinstance(paper_id, str)
        or len(paper_id) > MAX_PAPER_ID_CHARS
    ):
        return _error("invalid arXiv ID")

    resolved = resolve_stored_stem(paper_id, Path(settings.STORAGE_PATH))
    if resolved is None:
        return _error(
            f"Paper {paper_id} not found in storage. "
            "You may need to download it first using download_paper.",
            paper_id,
        )

    content = Path(settings.STORAGE_PATH, f"{resolved}.md").read_text(encoding="utf-8")
    bare = bare_arxiv_id(resolved)
    version = _sidecar_arxiv_version(
        resolved, Path(settings.STORAGE_PATH)
    ) or arxiv_version_suffix(resolved)
    versioned = f"{bare}{version}" if version else None
    return bare, version, versioned, content


def _coerce_max_sections(value: Any) -> int:
    try:
        return min(MAX_RETURNED_SECTIONS, max(1, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_SECTIONS


def _coerce_start(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _coerce_max_passages(value: Any) -> int:
    try:
        return min(MAX_RETURNED_PASSAGES, max(1, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PASSAGES


def _coerce_passage_chars(value: Any) -> int:
    try:
        return min(MAX_PASSAGE_CHARS, max(64, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PASSAGE_CHARS


# Near-duplicate passages: windows shifted by ~80–160 chars share most text.
PASSAGE_OVERLAP_THRESHOLD = 0.5
# Cap candidate scan so pathological queries (e.g. single letter) stay cheap.
MAX_PASSAGE_CANDIDATES = 500


def _passage_overlap_ratio(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """Fraction of the shorter window that overlaps the other (0..1)."""
    overlap = max(0, min(a_end, b_end) - max(a_start, b_start))
    if overlap == 0:
        return 0.0
    shorter = min(a_end - a_start, b_end - b_start)
    return overlap / shorter if shorter else 0.0


def _passages_heavily_overlap(
    candidate: dict[str, Any], selected: list[dict[str, Any]]
) -> bool:
    for prior in selected:
        if (
            _passage_overlap_ratio(
                candidate["start"],
                candidate["end"],
                prior["start"],
                prior["end"],
            )
            > PASSAGE_OVERLAP_THRESHOLD
        ):
            return True
    return False


def _select_diverse_passages(
    candidates: list[dict[str, Any]], max_passages: int
) -> list[dict[str, Any]]:
    """Pick up to max_passages preferring new sections, then fill non-overlaps."""
    if max_passages <= 0 or not candidates:
        return []

    selected: list[dict[str, Any]] = []
    seen_sections: set[str] = set()

    # Pass 1: one (non-overlapping) hit per section when possible.
    for cand in candidates:
        if len(selected) >= max_passages:
            break
        sid = cand.get("section_id")
        if sid is not None and sid in seen_sections:
            continue
        if _passages_heavily_overlap(cand, selected):
            continue
        selected.append(cand)
        if sid is not None:
            seen_sections.add(sid)

    # Pass 2: fill remaining slots with any non-overlapping later hits.
    if len(selected) < max_passages:
        selected_ids = {id(p) for p in selected}
        for cand in candidates:
            if len(selected) >= max_passages:
                break
            if id(cand) in selected_ids:
                continue
            if _passages_heavily_overlap(cand, selected):
                continue
            selected.append(cand)

    selected.sort(key=lambda p: (p["match_start"], p["start"]))
    return selected


def search_passages(
    content: str,
    sections: list[MdSection],
    query: str,
    *,
    max_passages: int,
    passage_chars: int,
) -> list[dict[str, Any]]:
    """Return bounded case-insensitive substring matches with source coordinates.

    Suppresses high-overlap near-duplicates and prefers section-diverse hits
    while still respecting ``max_passages``.
    """
    if not query:
        return []
    haystack = content.casefold()
    needle = query.casefold()
    if not needle:
        return []

    candidates: list[dict[str, Any]] = []
    search_from = 0
    half = max(32, passage_chars // 2)

    while len(candidates) < MAX_PASSAGE_CANDIDATES:
        idx = haystack.find(needle, search_from)
        if idx < 0:
            break
        match_end = idx + len(needle)
        excerpt_start = max(0, idx - half)
        excerpt_end = min(len(content), match_end + half)
        # Keep excerpt within passage_chars budget centered on the match.
        if excerpt_end - excerpt_start > passage_chars:
            overflow = excerpt_end - excerpt_start - passage_chars
            left_cut = overflow // 2
            right_cut = overflow - left_cut
            excerpt_start += left_cut
            excerpt_end -= right_cut

        section = _section_for_offset(sections, idx)
        excerpt = content[excerpt_start:excerpt_end]
        candidates.append(
            {
                "start": excerpt_start,
                "end": excerpt_end,
                "match_start": idx,
                "match_end": match_end,
                "section_id": section.section_id if section else None,
                "section_title": section.title if section else None,
                "excerpt": excerpt,
                "excerpt_chars": len(excerpt),
            }
        )
        # Advance past this match; nearby later hits become candidates for
        # overlap/section filtering below.
        search_from = match_end if match_end > search_from else search_from + 1

    return _select_diverse_passages(candidates, max_passages)


outline_tool = types.Tool(
    name="get_paper_outline",
    annotations=ToolAnnotations(readOnlyHint=True),
    description=(
        "Return a paginated heading outline for a downloaded paper (markdown). "
        "Stable hierarchical section IDs; use read_paper_section to fetch one."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": _paper_id_property(),
            "start": {
                "type": "integer",
                "minimum": 0,
                "description": "Zero-based section index (default 0)",
            },
            "max_sections": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_RETURNED_SECTIONS,
                "description": f"Maximum headings to return (default {DEFAULT_MAX_SECTIONS})",
            },
        },
        "required": ["paper_id"],
        "additionalProperties": False,
    },
)

read_section_tool = types.Tool(
    name="read_paper_section",
    annotations=ToolAnnotations(readOnlyHint=True),
    description=(
        "Return one bounded markdown section by outline ID (or unique title). "
        "Does not include sibling or parent sections."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": _paper_id_property(),
            "section_id": {
                "type": "string",
                "maxLength": MAX_SECTION_ID_CHARS,
                "description": "Section ID from get_paper_outline, or unique title",
            },
            **_page_properties(),
        },
        "required": ["paper_id", "section_id"],
        "additionalProperties": False,
    },
)

search_text_tool = types.Tool(
    name="search_paper_text",
    annotations=ToolAnnotations(readOnlyHint=True),
    description=(
        "Search a downloaded paper for bounded matching passages with "
        "section/source offsets. Suppresses high-overlap near-duplicates and "
        "prefers section-diverse hits. Lightweight substring search; no Torch."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": _paper_id_property(),
            "query": {
                "type": "string",
                "maxLength": MAX_QUERY_CHARS,
                "description": "Case-insensitive substring to find",
            },
            "max_passages": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_RETURNED_PASSAGES,
                "description": f"Maximum passages to return (default {DEFAULT_MAX_PASSAGES})",
            },
            "passage_chars": {
                "type": "integer",
                "minimum": 64,
                "maximum": MAX_PASSAGE_CHARS,
                "description": f"Max characters per excerpt (default {DEFAULT_PASSAGE_CHARS})",
            },
        },
        "required": ["paper_id", "query"],
        "additionalProperties": False,
    },
)


async def handle_get_paper_outline(
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    """Return a paginated outline of markdown headings for a stored paper."""
    loaded = _load_paper(arguments.get("paper_id"))
    if isinstance(loaded, list):
        return loaded
    bare, version, versioned, content = loaded
    try:
        sections = parse_markdown_sections(content)
        start = _coerce_start(arguments.get("start", 0))
        max_sections = _coerce_max_sections(
            arguments.get("max_sections", DEFAULT_MAX_SECTIONS)
        )
        page = sections[start : start + max_sections]
        next_start = start + len(page)
        payload = {
            "status": "success",
            "paper_id": bare,
            "arxiv_version": version,
            "versioned_id": versioned,
            "total_sections": len(sections),
            "start": start,
            "returned_sections": len(page),
            "next_start": next_start if next_start < len(sections) else None,
            "is_truncated": next_start < len(sections),
            "sections": [
                {
                    "id": item.section_id,
                    "level": item.level,
                    "title": item.title,
                    "start": item.start,
                    "end": item.end,
                }
                for item in page
            ],
        }
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception:
        return _error("Paper outline retrieval failed", bare)


async def handle_read_paper_section(
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    """Return one bounded markdown section by ID or unique title."""
    loaded = _load_paper(arguments.get("paper_id"))
    if isinstance(loaded, list):
        return loaded
    bare, version, versioned, content = loaded

    section_id = arguments.get("section_id")
    if not isinstance(section_id, str) or not section_id.strip():
        return _error("section_id is required", bare)
    if len(section_id) > MAX_SECTION_ID_CHARS:
        return _error(f"section_id exceeds {MAX_SECTION_ID_CHARS} characters", bare)

    try:
        sections = parse_markdown_sections(content)
        section = _find_section(sections, section_id)
        if section is None:
            return _error(
                f"Section {section_id!r} not found; call get_paper_outline first",
                bare,
            )
        body = content[section.start : section.end].rstrip()
        payload: dict[str, Any] = {
            "status": "success",
            "paper_id": bare,
            "arxiv_version": version,
            "versioned_id": versioned,
            "section": {
                "id": section.section_id,
                "level": section.level,
                "title": section.title,
                "start": section.start,
                "end": section.end,
            },
        }
        add_content_payload(payload, body, arguments, CONTENT_WARNING)
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception:
        return _error("Paper section retrieval failed", bare)


async def handle_search_paper_text(
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    """Search stored markdown for bounded passages with source coordinates."""
    loaded = _load_paper(arguments.get("paper_id"))
    if isinstance(loaded, list):
        return loaded
    bare, version, versioned, content = loaded

    query = arguments.get("query")
    if not isinstance(query, str):
        return _error("query is required", bare)
    if len(query) > MAX_QUERY_CHARS:
        return _error(f"query exceeds {MAX_QUERY_CHARS} characters", bare)

    try:
        sections = parse_markdown_sections(content)
        max_passages = _coerce_max_passages(
            arguments.get("max_passages", DEFAULT_MAX_PASSAGES)
        )
        passage_chars = _coerce_passage_chars(
            arguments.get("passage_chars", DEFAULT_PASSAGE_CHARS)
        )
        passages = search_passages(
            content,
            sections,
            query,
            max_passages=max_passages,
            passage_chars=passage_chars,
        )
        payload = {
            "status": "success",
            "paper_id": bare,
            "arxiv_version": version,
            "versioned_id": versioned,
            "query": query,
            "returned_passages": len(passages),
            "max_passages": max_passages,
            "passage_chars": passage_chars,
            "passages": passages,
        }
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception:
        return _error("Paper text search failed", bare)
