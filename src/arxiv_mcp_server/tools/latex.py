"""Safe, bounded retrieval of original LaTeX sources from arXiv."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

import httpx
import mcp.types as types
from mcp.types import ToolAnnotations

from ..arxiv_api import ARXIV_RATE_LIMITER
from ..config import Settings
from .content import LATEX_CONTENT_WARNING, add_content_payload
from .latex_archive import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_PATH_BYTES,
    MAX_ARCHIVE_PATH_DEPTH,
    MAX_MEMBER_BYTES,
    MAX_TEX_FILES,
    MAX_TOTAL_TEX_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    LatexSourceError,
    SourceArchiveLimitError,
    UnsafeSourceArchiveError,
    _download_source_archive as _download_source_archive_impl,
    _extract_tex_files as _extract_tex_files_impl,
    _main_file_score,
    _read_plain_gzip,
    _safe_member_candidate,
    _safe_member_name,
)
from .latex_flatten import (
    MAX_FLATTENED_CHARS,
    MAX_INCLUDE_DEPTH,
    MAX_MACRO_ROUNDS,
    MAX_SECTION_COUNT,
    MAX_SECTION_TITLE_CHARS,
    LatexSection,
    _collect_macros,
    _extract_section,
    _find_section,
    _flatten_source as _flatten_source_impl,
    _flatten_source_with_unmatched as _flatten_source_with_unmatched_impl,
    _mask_tex_comments,
    _parse_sections,
    _resolve_include,
)
from .arxiv_ids import parse_arxiv_id

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

DEFAULT_MAX_SECTIONS = 100
MAX_RETURNED_SECTIONS = 200
CACHE_FORMAT_VERSION = 2
MAX_PAPER_ID_CHARS = 40
MAX_SECTION_ID_CHARS = 200
DEFAULT_MAX_CHARS = 12_000
MAX_RETURN_CHARS = 50_000
MAX_REPORTED_UNMATCHED = 8

_SOURCE_LOCKS = tuple(threading.Lock() for _ in range(64))


def _apply_archive_overrides() -> None:
    """Push test monkeypatches from this module onto archive helpers."""
    from . import latex_archive as archive

    archive.MAX_ARCHIVE_BYTES = MAX_ARCHIVE_BYTES
    archive.MAX_ARCHIVE_MEMBERS = MAX_ARCHIVE_MEMBERS
    archive.MAX_ARCHIVE_PATH_BYTES = MAX_ARCHIVE_PATH_BYTES
    archive.MAX_ARCHIVE_PATH_DEPTH = MAX_ARCHIVE_PATH_DEPTH
    archive.MAX_MEMBER_BYTES = MAX_MEMBER_BYTES
    archive.MAX_TOTAL_UNCOMPRESSED_BYTES = MAX_TOTAL_UNCOMPRESSED_BYTES
    archive.MAX_TEX_FILES = MAX_TEX_FILES
    archive.MAX_TOTAL_TEX_BYTES = MAX_TOTAL_TEX_BYTES


def _apply_flatten_overrides() -> None:
    """Push test monkeypatches from this module onto flatten helpers."""
    from . import latex_flatten as flatten

    flatten.MAX_FLATTENED_CHARS = MAX_FLATTENED_CHARS
    flatten.MAX_INCLUDE_DEPTH = MAX_INCLUDE_DEPTH
    flatten.MAX_MACRO_ROUNDS = MAX_MACRO_ROUNDS
    flatten.MAX_SECTION_COUNT = MAX_SECTION_COUNT
    flatten.MAX_SECTION_TITLE_CHARS = MAX_SECTION_TITLE_CHARS


def _download_source_archive(paper_id: str) -> bytes:
    _apply_archive_overrides()
    return _download_source_archive_impl(paper_id)


def _extract_tex_files(data: bytes) -> dict[str, str]:
    _apply_archive_overrides()
    return _extract_tex_files_impl(data)


def _flatten_source(files: dict[str, str]) -> tuple[str, str]:
    _apply_flatten_overrides()
    return _flatten_source_impl(files)


def _flatten_source_with_unmatched(
    files: dict[str, str],
) -> tuple[str, str, tuple[str, ...]]:
    _apply_flatten_overrides()
    return _flatten_source_with_unmatched_impl(files)


@dataclass(frozen=True)
class LatexSource:
    content: str
    main_file: str
    source_files: int
    unmatched_includes: tuple[str, ...] = ()


def _error(message: str, paper_id: str | None = None) -> list[types.TextContent]:
    payload: dict[str, Any] = {"status": "error", "message": message}
    if paper_id:
        payload["paper_id"] = paper_id
    return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]


def _normalized_paper_id(arguments: dict[str, Any]) -> str | None:
    value = arguments.get("paper_id")
    if not isinstance(value, str):
        return None
    paper_id = parse_arxiv_id(value)
    if paper_id is None or len(paper_id) > MAX_PAPER_ID_CHARS:
        return None
    return paper_id


def _bounded_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    bounded = dict(arguments)
    if "max_chars" not in bounded:
        bounded["max_chars"] = DEFAULT_MAX_CHARS
    else:
        try:
            bounded["max_chars"] = min(
                MAX_RETURN_CHARS, max(1, int(bounded["max_chars"]))
            )
        except (TypeError, ValueError):
            bounded["max_chars"] = DEFAULT_MAX_CHARS
    return bounded


def _cache_path(paper_id: str) -> Path:
    safe_id = paper_id.replace("/", "__")
    directory = Path(settings.STORAGE_PATH) / ".latex"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe_id}.json"


def _load_cached_source(paper_id: str) -> LatexSource | None:
    path = _cache_path(paper_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        content = payload["content"]
        main_file = payload["main_file"]
        source_files = payload["source_files"]
        if payload.get("cache_format") != CACHE_FORMAT_VERSION:
            raise ValueError("stale cache format")
        if not isinstance(content, str) or len(content) > MAX_FLATTENED_CHARS:
            raise ValueError("invalid cached content")
        if not isinstance(main_file, str) or not main_file or len(main_file) > 512:
            raise ValueError("invalid cached main file")
        if (
            not isinstance(source_files, int)
            or isinstance(source_files, bool)
            or not 1 <= source_files <= MAX_TEX_FILES
        ):
            raise ValueError("invalid cached source count")
        unmatched = payload.get("unmatched_includes", [])
        if (
            not isinstance(unmatched, list)
            or len(unmatched) > 64
            or not all(
                isinstance(item, str) and 0 < len(item) <= 300 for item in unmatched
            )
        ):
            unmatched = []
        return LatexSource(content, main_file, source_files, tuple(unmatched))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def _write_cached_source(paper_id: str, source: LatexSource) -> None:
    path = _cache_path(paper_id)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".part"
    )
    os.close(descriptor)
    staging = Path(name)
    try:
        staging.write_text(
            json.dumps(
                {
                    "cache_format": CACHE_FORMAT_VERSION,
                    "content": source.content,
                    "main_file": source.main_file,
                    "source_files": source.source_files,
                    "unmatched_includes": list(source.unmatched_includes[:32]),
                }
            ),
            encoding="utf-8",
        )
        staging.replace(path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def _load_source(paper_id: str) -> LatexSource:
    lock = _SOURCE_LOCKS[hash(paper_id) % len(_SOURCE_LOCKS)]
    with lock:
        if cached := _load_cached_source(paper_id):
            return cached
        archive = _download_source_archive(paper_id)
        files = _extract_tex_files(archive)
        content, main_file, unmatched = _flatten_source_with_unmatched(files)
        source = LatexSource(content, main_file, len(files), unmatched)
        _write_cached_source(paper_id, source)
        return source


def _paper_id_property() -> dict[str, Any]:
    return {
        "type": "string",
        "maxLength": 40,
        "description": "Validated modern or legacy arXiv paper ID",
    }


def _page_properties() -> dict[str, Any]:
    return {
        "start": {
            "type": "integer",
            "minimum": 0,
            "description": "Zero-based character offset within this source or section",
        },
        "max_chars": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_RETURN_CHARS,
            "description": f"Maximum source characters to return (default {DEFAULT_MAX_CHARS})",
        },
    }


get_paper_latex_tool = types.Tool(
    name="get_paper_latex",
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
    description=(
        "Download, safely process, cache, and return bounded original LaTeX source. "
        "Use section tools for targeted reading."
    ),
    inputSchema={
        "type": "object",
        "properties": {"paper_id": _paper_id_property(), **_page_properties()},
        "required": ["paper_id"],
        "additionalProperties": False,
    },
)

list_paper_latex_sections_tool = types.Tool(
    name="list_paper_latex_sections",
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
    description="Return a compact outline of headings from original LaTeX source.",
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

get_paper_latex_section_tool = types.Tool(
    name="get_paper_latex_section",
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
    description="Return one bounded LaTeX section by outline ID or title (whitespace/case normalized; macros expanded).",
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": _paper_id_property(),
            "section_id": {
                "type": "string",
                "maxLength": 200,
                "description": "Section ID from list_paper_latex_sections or section title",
            },
            **_page_properties(),
        },
        "required": ["paper_id", "section_id"],
        "additionalProperties": False,
    },
)


def _empty_outline_message(source: LatexSource) -> str:
    message = (
        "LaTeX outline is empty; include/import commands did not resolve "
        "to section headings. Use read_paper or HTML instead."
    )
    if source.unmatched_includes:
        shown = ", ".join(source.unmatched_includes[:MAX_REPORTED_UNMATCHED])
        message += f" Unresolved include commands: {shown}."
    return message


async def handle_get_paper_latex(
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    paper_id = _normalized_paper_id(arguments)
    if paper_id is None:
        return _error("invalid arXiv ID")
    try:
        source = await asyncio.to_thread(_load_source, paper_id)
        payload: dict[str, Any] = {
            "status": "success",
            "paper_id": paper_id,
            "main_file": source.main_file,
            "source_files": source.source_files,
        }
        add_content_payload(
            payload,
            source.content,
            _bounded_arguments(arguments),
            LATEX_CONTENT_WARNING,
        )
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        message = (
            "LaTeX source is unavailable for this paper"
            if status in {404, 403}
            else f"arXiv source request failed with HTTP {status}"
        )
        return _error(message, paper_id)
    except LatexSourceError as exc:
        return _error(str(exc), paper_id)
    except Exception as exc:
        logger.exception("LaTeX source retrieval failed for %s", paper_id)
        return _error("LaTeX source retrieval failed", paper_id)


async def handle_list_paper_latex_sections(
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    paper_id = _normalized_paper_id(arguments)
    if paper_id is None:
        return _error("invalid arXiv ID")
    try:
        source = await asyncio.to_thread(_load_source, paper_id)
        sections = _parse_sections(source.content)
        if not sections:
            return _error(_empty_outline_message(source), paper_id)
        try:
            start = max(0, int(arguments.get("start", 0)))
        except (TypeError, ValueError):
            start = 0
        try:
            max_sections = min(
                MAX_RETURNED_SECTIONS,
                max(1, int(arguments.get("max_sections", DEFAULT_MAX_SECTIONS))),
            )
        except (TypeError, ValueError):
            max_sections = DEFAULT_MAX_SECTIONS
        page = sections[start : start + max_sections]
        next_start = start + len(page)
        payload = {
            "status": "success",
            "paper_id": paper_id,
            "main_file": source.main_file,
            "total_sections": len(sections),
            "start": start,
            "returned_sections": len(page),
            "next_start": next_start if next_start < len(sections) else None,
            "is_truncated": next_start < len(sections),
            "sections": [
                {"id": item.section_id, "level": item.level, "title": item.title}
                for item in page
            ],
        }
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]
    except httpx.HTTPStatusError as exc:
        return _error(
            f"arXiv source request failed with HTTP {exc.response.status_code}",
            paper_id,
        )
    except LatexSourceError as exc:
        return _error(str(exc), paper_id)
    except Exception as exc:
        logger.exception("LaTeX outline retrieval failed for %s", paper_id)
        return _error("LaTeX outline retrieval failed", paper_id)


async def handle_get_paper_latex_section(
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    paper_id = _normalized_paper_id(arguments)
    if paper_id is None:
        return _error("invalid arXiv ID")
    section_id = arguments.get("section_id")
    if not isinstance(section_id, str) or not section_id.strip():
        return _error("section_id is required", paper_id)
    if len(section_id) > MAX_SECTION_ID_CHARS:
        return _error(f"section_id exceeds {MAX_SECTION_ID_CHARS} characters", paper_id)
    try:
        source = await asyncio.to_thread(_load_source, paper_id)
        sections = _parse_sections(source.content)
        macros = _collect_macros(source.content, _mask_tex_comments(source.content))
        section = _find_section(sections, section_id, macros)
        if section is None:
            return _error(
                f"LaTeX section {section_id!r} not found; call list_paper_latex_sections first",
                paper_id,
            )
        content = source.content[section.start : section.end].rstrip()
        payload: dict[str, Any] = {
            "status": "success",
            "paper_id": paper_id,
            "section": {
                "id": section.section_id,
                "level": section.level,
                "title": section.title,
            },
        }
        add_content_payload(
            payload,
            content,
            _bounded_arguments(arguments),
            LATEX_CONTENT_WARNING,
        )
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]
    except httpx.HTTPStatusError as exc:
        return _error(
            f"arXiv source request failed with HTTP {exc.response.status_code}",
            paper_id,
        )
    except LatexSourceError as exc:
        return _error(str(exc), paper_id)
    except Exception as exc:
        logger.exception("LaTeX section retrieval failed for %s", paper_id)
        return _error("LaTeX section retrieval failed", paper_id)
