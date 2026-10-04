"""Download functionality for the arXiv MCP server."""

import arxiv
import gc
import json
import asyncio
import httpx
import random
import requests
import time
from html.parser import HTMLParser
import re
from pathlib import Path
from typing import Dict, Any, List
import mcp.types as types
from mcp.types import ToolAnnotations
from ..config import Settings, get_arxiv_client
from ..arxiv_api import (
    ARXIV_RATE_LIMITER,
    stream_pdf_to_path,
    ArxivRateLimitError,
    ArxivTimeoutError,
    retry_with_backoff,
)
from .content import add_content_payload, CONTENT_WARNING
from .arxiv_ids import (
    arxiv_version_number,
    arxiv_version_suffix,
    bare_arxiv_id,
    filesystem_arxiv_stem,
    logical_arxiv_id_from_stem,
    parse_arxiv_id,
)
from .list_papers import resolve_stored_stem
from .list_papers import save_paper_metadata
from .search import (
    ARXIV_API_URL,
    ARXIV_NS,
    _rate_limited_get,
    _rate_limited_response,
)
import logging
import threading
import xml.etree.ElementTree as ET

pymupdf4llm: Any = None
fitz: Any = None
_pdf_available: bool | None = None


def _load_pdf_dependencies() -> bool:
    """Load PDF conversion modules only when the fallback path is invoked."""
    global pymupdf4llm, fitz, _pdf_available
    if _pdf_available is not None:
        return _pdf_available
    try:
        import fitz as fitz_module
        import pymupdf4llm as pymupdf4llm_module
    except ImportError:  # pragma: no cover - environment dependent
        _pdf_available = False
        return False
    fitz = fitz_module
    pymupdf4llm = pymupdf4llm_module
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)
    _pdf_available = True
    return True


logger = logging.getLogger("arxiv-mcp-server")

# Serialise background indexing to avoid hammering the GPU/CPU when multiple
# papers are downloaded in parallel (issue #68). Tasks are explicitly owned so
# server shutdown can cancel and drain them deterministically.
_index_semaphore: asyncio.Semaphore | None = None
_index_tasks: set[asyncio.Task[None]] = set()
# Fixed-size lock striping bounds memory while preventing same-paper PDF races.
_pdf_conversion_locks = tuple(threading.Lock() for _ in range(64))


def _get_index_semaphore() -> asyncio.Semaphore:
    """Return the module-level indexing semaphore, creating it lazily."""
    global _index_semaphore
    if _index_semaphore is None:
        _index_semaphore = asyncio.Semaphore(1)
    return _index_semaphore


def _semantic_dependencies_available() -> bool:
    """Check pro dependencies only when automatic indexing is requested."""
    from .semantic_search import _dependency_error

    return _dependency_error() is None


async def _run_index_by_id(paper_id: str) -> None:
    """Acquire the index semaphore then index a paper in a worker thread."""
    from .semantic_search import index_paper_by_id

    async with _get_index_semaphore():
        await asyncio.to_thread(index_paper_by_id, paper_id)


async def _run_index_from_result(arxiv_result) -> None:
    """Acquire the index semaphore then index a result in a worker thread."""
    from .semantic_search import index_paper_from_result

    async with _get_index_semaphore():
        await asyncio.to_thread(index_paper_from_result, arxiv_result)


def _finish_index_task(task: asyncio.Task[None]) -> None:
    """Release task ownership and consume failures to avoid teardown warnings."""
    _index_tasks.discard(task)
    if not task.cancelled():
        task.exception()


def _track_index_task(coroutine) -> None:
    """Create and retain one background indexing task when pro deps exist."""
    if not _semantic_dependencies_available():
        coroutine.close()
        return
    try:
        task = asyncio.create_task(coroutine)
    except RuntimeError:
        coroutine.close()
        return
    _index_tasks.add(task)
    task.add_done_callback(_finish_index_task)


async def shutdown_background_tasks() -> None:
    """Wait for owned indexing workers before releasing shared resources."""
    global _index_semaphore
    tasks = list(_index_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _index_tasks.clear()
    # Semaphores are event-loop-bound once contended; do not reuse one after
    # this server lifecycle ends.
    _index_semaphore = None


settings = Settings()

# Bump when HTML extraction changes so cached markdown is treated as stale
# and re-downloaded without requiring the caller to pass force=true.
# #265 already claimed 6 for decorative titles; this Switch noise cleanup
# bumps to 7 so both changes invalidate caches (#175).
EXTRACTOR_VERSION = 7


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


class _ArticleTextExtractor(HTMLParser):
    """Extract readable paper text from an arXiv HTML page.

    Strategy:
      - Prefer ``<article>`` body text when present so site chrome outside
        the paper is dropped (banners, report-issue dialog, watermarks).
      - Skip script/style/nav/header/footer plus arXiv UI widgets.
      - Skip author-note chrome (Thanks/ORCID/affiliation/email blocks).
      - Skip conference/DOI/ISBN/CCS pubnotes and date/license chrome.
      - Coalesce decorative letter-span titles into one line (keep
        subtitle colons attached); coalesce author lines; drop
        affiliation superscripts and TeX superscript debris in the
        author block; drop pre-title bylines that duplicate authors.
      - Skip journal heading/shortheadings/editor note chrome.
      - Skip footnotemark markers (class, role, or the literal token).
      - Join decorative breaks in bracket cites, figure/table refs, and
        parenthetical citations where HTML split them across lines.
      - Skip license/permission one-liners and ICML/LaTeX page-layout
        style warnings (marginparsep and similar) that appear before the
        title.
      - Keep math once: prefer ``alttext``, otherwise MathML without TeX
        ``<annotation>`` duplicates; normalize common ``\\times`` noise.
    """

    SKIP_TAGS = {
        "script",
        "style",
        "head",
        "nav",
        "header",
        "footer",
        "aside",
        "dialog",
        "form",
        "noscript",
        "template",
        "button",
        "input",
        "select",
        "textarea",
        "label",
        "annotation",
        "annotation-xml",
    }
    SKIP_CLASSES = {
        "ds-announcement",
        "arxiv-html-header",
        "ds-site-footer",
        "infobox",
        "ltx_author_notes",
        "ltx_contact",
        "sr-only",
        "ltx_page_logo",
        "html-header-logo",
        "ltx_role_footnotemark",
        "ltx_note_mark",
        "ltx_note_type",
        "ltx_tag_note",
        # ACM/IEEE front-matter dumped into the title (CCS, DOI, ISBN, …).
        "ltx_pubnotes",
        "ltx_pubnote",
        "ltx_dates",
        "ltx_role_cc-license",
        # Journal/PDF chrome notes (running headers, editor, page marks).
        "ltx_role_heading",
        "ltx_role_shortheadings",
        "ltx_role_firstpage",
        "ltx_role_editor",
        "ltx_role_newpage",
        "ltx_role_refnum",
    }
    FOOTNOTEMARK_TOKEN = "footnotemark"
    PERMISSION_MARKERS = (
        "permission to reproduce",
        "hereby grants permission",
        "tables and figures in this paper solely for use",
    )
    # ICML/LaTeX page-layout warnings that latexml dumps before the title.
    STYLE_WARNING_MARKERS = (
        "marginparsep has been altered",
        "topmargin has been altered",
        "marginparpush has been altered",
        "page layout violates the icml style",
        "please do not change the page layout",
        "packages like geometry",
        "reliably undo arbitrary changes to the style",
        "layout-changing commands",
    )
    SKIP_IDS = {
        "modal-form",
        "announcement-banner",
        "infobox",
        "watermark-tr",
    }
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    # Empty-base superscripts / textsuperscript debris used as affiliation marks.
    _AFFILIATION_MATH_RE = re.compile(
        r"^\{\}\^|textsuperscript",
        re.IGNORECASE,
    )
    _AUTHOR_STOPWORDS = frozenset({"and", "or", "the", "of", "for"})

    def __init__(self):
        super().__init__()
        self._skip_depth: int = 0
        self._skip_stack: list[bool] = []
        self._article_depth: int = 0
        self._article_chunks: list[str] = []
        self._body_chunks: list[str] = []
        self._seen_title: bool = False
        self._authors_depth: int = 0
        self._authors_stack: list[bool] = []
        self._author_buf: list[str] = []
        self._title_depth: int = 0
        self._title_stack: list[bool] = []
        self._title_buf: list[str] = []

    def _should_skip(self, tag: str, attr_map: dict[str, str]) -> bool:
        if tag in self.SKIP_TAGS:
            return True
        classes = set((attr_map.get("class") or "").split())
        if classes & self.SKIP_CLASSES:
            return True
        if any(self.FOOTNOTEMARK_TOKEN in cls.lower() for cls in classes):
            return True
        for value in attr_map.values():
            if value and self.FOOTNOTEMARK_TOKEN in str(value).lower():
                return True
        elem_id = attr_map.get("id") or ""
        return elem_id in self.SKIP_IDS

    def _is_pre_title_chrome(self, text: str) -> bool:
        """True for permission/license lines or ICML style warnings."""
        lowered = text.lower()
        if any(marker in lowered for marker in self.PERMISSION_MARKERS):
            return True
        return any(marker in lowered for marker in self.STYLE_WARNING_MARKERS)

    @staticmethod
    def _normalize_math_alttext(alttext: str) -> str:
        """Reduce common TeX extraction noise in math alttext."""
        # ``\times`` alone or embedded (e.g. ``2.0\times``, ``L\times E``).
        return alttext.replace(r"\times", "\u00d7")

    @classmethod
    def _author_name_tokens(cls, text: str) -> set[str]:
        """Alphabetic tokens from an author/byline string (for dedupe)."""
        return {
            tok
            for tok in re.findall(r"[A-Za-z]+", text.lower())
            if len(tok) > 1 and tok not in cls._AUTHOR_STOPWORDS
        }

    def _drop_duplicate_author_bylines(self, author_line: str) -> None:
        """Remove earlier chunks that only repeat the author names.

        Some latexml/ar5iv pages emit a plain-paragraph byline before the
        title in addition to ``ltx_authors`` (Switch Transformers).
        """
        name_tokens = self._author_name_tokens(author_line)
        if not name_tokens:
            return
        chunks = self._article_chunks if self._article_depth > 0 else self._body_chunks
        kept: list[str] = []
        for chunk in chunks:
            chunk_tokens = self._author_name_tokens(chunk)
            if chunk_tokens and chunk_tokens <= name_tokens and len(chunk) < 300:
                continue
            kept.append(chunk)
        if self._article_depth > 0:
            self._article_chunks = kept
        else:
            self._body_chunks = kept

    def _flush_authors(self) -> None:
        """Join buffered author tokens into one coherent line."""
        if not self._author_buf:
            return
        line = " ".join(self._author_buf)
        line = re.sub(r"\s+,", ",", line)
        line = re.sub(r",\s*", ", ", line)
        line = re.sub(r"\s+", " ", line).strip(" ,")
        self._author_buf = []
        if line:
            self._drop_duplicate_author_bylines(line)
            self._append_chunk(line)

    def _flush_title(self) -> None:
        """Join decorative title letter spans into one coherent line."""
        if not self._title_buf:
            return
        # Concatenate raw pieces so underlined acronym letters reattach
        # to the rest of each word (D+ata- → Data-), then collapse space.
        line = re.sub(r"\s+", " ", "".join(self._title_buf)).strip()
        self._title_buf = []
        if line:
            self._append_chunk(line)

    def _append_chunk(self, text: str) -> None:
        if self._article_depth > 0:
            self._article_chunks.append(text)
        else:
            self._body_chunks.append(text)

    def _emit(self, text: str) -> None:
        if self._skip_depth or not text:
            return
        if self.FOOTNOTEMARK_TOKEN in text.lower():
            return
        if not self._seen_title and self._is_pre_title_chrome(text):
            return
        if self._title_depth > 0:
            self._title_buf.append(text)
            return
        if self._authors_depth > 0:
            self._author_buf.append(text)
            return
        self._append_chunk(text)

    def handle_starttag(self, tag: str, attrs):
        attr_map = dict(attrs)
        if tag == "article":
            self._article_depth += 1
        classes = set((attr_map.get("class") or "").split())
        if tag in {"h1", "h2"} or "ltx_title" in classes:
            self._seen_title = True

        # Document title only — not abstract/section ``ltx_title_*``.
        entering_title = tag == "h1" or "ltx_title_document" in classes
        if entering_title:
            self._title_depth += 1

        entering_authors = "ltx_authors" in classes
        if entering_authors:
            self._authors_depth += 1
        # latexml inserts ``ltx_author_before`` between creators; keep commas.
        if self._authors_depth > 0 and "ltx_author_before" in classes:
            if self._author_buf and self._author_buf[-1] != ",":
                self._author_buf.append(",")

        # Void elements have no children. Incrementing skip_depth for
        # <input> etc. and never seeing an end tag left the rest of the
        # document, including <article>, permanently skipped. Do not push
        # authors/title stacks either — void tags have no matching endtag.
        if tag in self.VOID_TAGS:
            return

        skip = self._should_skip(tag, attr_map)
        # Affiliation superscripts inside the author block (e.g. "2", "1,5").
        if self._authors_depth > 0 and (tag == "sup" or "ltx_sup" in classes):
            skip = True

        if tag == "math":
            alttext = (attr_map.get("alttext") or "").strip()
            if alttext:
                if self._AFFILIATION_MATH_RE.search(alttext):
                    # Drop empty-base superscript / textsuperscript debris.
                    skip = True
                else:
                    # Emit TeX/alt once and ignore MathML + annotation children.
                    self._emit(self._normalize_math_alttext(alttext))
                    skip = True

        if skip:
            self._skip_depth += 1
        self._skip_stack.append(skip)
        self._title_stack.append(entering_title)
        self._authors_stack.append(entering_authors)

    def handle_endtag(self, tag: str):
        if self._skip_stack and self._skip_stack.pop():
            self._skip_depth = max(0, self._skip_depth - 1)
        if self._title_stack and self._title_stack.pop():
            self._title_depth = max(0, self._title_depth - 1)
            if self._title_depth == 0:
                self._flush_title()
        if self._authors_stack and self._authors_stack.pop():
            self._authors_depth = max(0, self._authors_depth - 1)
            if self._authors_depth == 0:
                self._flush_authors()
        if tag == "article" and self._article_depth > 0:
            self._article_depth -= 1

    def handle_startendtag(self, tag: str, attrs):
        if tag in self.VOID_TAGS:
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str):
        # Title letter spans must keep surrounding whitespace so
        # ``D``+``ata-`` reassemble; do not strip until flush.
        if self._title_depth > 0 and not self._skip_depth:
            if data:
                self._title_buf.append(data)
            return
        self._emit(data.strip())

    def get_text(self) -> str:
        if self._title_depth > 0:
            self._flush_title()
        if self._authors_depth > 0:
            self._flush_authors()
        chunks = self._article_chunks or self._body_chunks
        return "\n".join(chunks)


def _extract_article_fragment(html: str) -> str | None:
    """Return the first <article>…</article> slice when present."""
    lower = html.lower()
    start = lower.find("<article")
    if start < 0:
        return None
    end = lower.find("</article>", start)
    if end < 0:
        return html[start:]
    return html[start : end + len("</article>")]


def _join_split_ref_noise(text: str) -> str:
    """Rejoin decorative HTML line breaks in cites and figure/table refs.

    latexml often emits ``[``, ``1``, ``]`` or ``Fig.`` / ``2`` as separate
    text nodes; with newline-joined chunks that becomes ``[\n1\n]`` or
    ``Fig.\n2``. Parenthetical author-year cites split the same way.
    """
    # Bracket cites: [\n1\n] → [1]
    text = re.sub(r"\[\n(\d+)\n\]", r"[\1]", text)
    # Fig./Figure/Table/Section/Eq. N[+optional letter] on the next line(s).
    text = re.sub(
        r"\b(Fig\.|Figure|Table|Section|Sec\.|Eq\.|Equation)\n(\d+[a-zA-Z]?)\n",
        r"\1 \2 ",
        text,
    )
    text = re.sub(
        r"\b(Fig\.|Figure|Table|Section|Sec\.|Eq\.|Equation)\n(\d+[a-zA-Z]?)$",
        r"\1 \2",
        text,
        flags=re.MULTILINE,
    )

    def _join_paren_block(match: re.Match[str]) -> str:
        parts = [p.strip() for p in match.group(1).split("\n") if p.strip()]
        if not parts or any(len(p) >= 80 for p in parts):
            return match.group(0)
        out: list[str] = []
        for part in parts:
            if part in {";", ","}:
                if out:
                    out[-1] = out[-1] + part
                else:
                    out.append(part)
            elif out and out[-1].endswith((";", ",")):
                out[-1] = f"{out[-1]} {part}"
            else:
                out.append(part)
        return "(" + " ".join(out) + ")"

    # (\nAuthor year\n) and multi-cite (\nA\n;\nB\n) lists.
    text = re.sub(r"\(([^\n()]*(?:\n[^\n()]*)+)\)", _join_paren_block, text)
    return text


def _html_to_text(html: str) -> str:
    """Parse raw HTML and return cleaned paper text."""
    parser = _ArticleTextExtractor()
    parser.feed(_extract_article_fragment(html) or html)
    text = parser.get_text()
    # Math alttext like ``\\times`` becomes a lone ``×`` chunk; keep it on
    # the same line as the surrounding tokens (e.g. ``10× over``).
    text = re.sub(r"\n×\n", "× ", text)
    text = re.sub(r"\n×$", "×", text)
    text = re.sub(r"^×\n", "× ", text)
    return _join_split_ref_noise(text)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_paper_path(paper_id: str, suffix: str = ".md") -> Path:
    """Get the absolute file path for a paper with given suffix.

    Legacy slash-form IDs are mapped to a flat stem (``/`` -> ``__``) so the
    path stays under ``STORAGE_PATH`` without requiring category subdirectories.
    Parent directories are still created defensively for any nested suffix paths.
    """
    storage_path = Path(settings.STORAGE_PATH)
    storage_path.mkdir(parents=True, exist_ok=True)
    path = storage_path / f"{filesystem_arxiv_stem(paper_id)}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_extractor_version(paper_id: str) -> int | None:
    """Return the sidecar extractor version, or None if missing/unreadable."""
    path = get_paper_path(paper_id, ".meta.json")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("extractor_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    return version


def _is_fresh_cache(paper_id: str) -> bool:
    """True when cached markdown exists and the sidecar version is current."""
    stored = _read_extractor_version(paper_id)
    return stored is not None and stored >= EXTRACTOR_VERSION


def _read_arxiv_version(paper_id: str) -> str | None:
    """Return the sidecar arXiv version (e.g. ``v7``), or None."""
    path = get_paper_path(paper_id, ".meta.json")
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
    return arxiv_version_suffix(paper_id)


def _version_from_arxiv_result(paper) -> str | None:
    """Best-effort ``vN`` from an arXiv result short id."""
    short = getattr(paper, "get_short_id", None)
    if callable(short):
        try:
            return arxiv_version_suffix(str(short()))
        except Exception:
            return None
    return None


def _cleanup_versioned_aliases(storage_id: str) -> None:
    """Remove legacy versioned ``.md`` / sidecar files for the same bare ID."""
    storage = get_paper_path(storage_id, ".md").parent
    if not storage.exists():
        return
    for path in storage.iterdir():
        if not path.is_file():
            continue
        stem = path.name
        # Handle both ``id.md`` and ``id.meta.json``
        if stem.endswith(".meta.json"):
            paper_stem = stem[: -len(".meta.json")]
        elif path.suffix == ".md":
            paper_stem = path.stem
        else:
            continue
        logical_stem = logical_arxiv_id_from_stem(paper_stem)
        if logical_stem == storage_id:
            continue
        if bare_arxiv_id(logical_stem) != storage_id:
            continue
        try:
            path.unlink()
        except OSError:
            logger.warning("Could not remove legacy alias %s", path)


def _cache_satisfies_request(storage_stem: str, requested_id: str) -> bool:
    """True when a fresh cache entry can answer *requested_id*."""
    if not _is_fresh_cache(storage_stem):
        return False
    req_ver = arxiv_version_suffix(requested_id)
    if req_ver is None:
        return True
    stored_ver = _read_arxiv_version(storage_stem) or arxiv_version_suffix(storage_stem)
    return stored_ver is not None and stored_ver == req_ver


def _would_downgrade_cached_version(storage_stem: str, requested_id: str) -> bool:
    """True when *requested_id* is an older arXiv version than the bare cache.

    Used to block silent downgrades of the bare-ID store without force=true
    (issue #206). Unknown stored versions never count as a downgrade.
    """
    req_ver = arxiv_version_suffix(requested_id)
    if req_ver is None:
        return False
    stored_ver = _read_arxiv_version(storage_stem)
    if stored_ver is None:
        return False
    return arxiv_version_number(req_ver) < arxiv_version_number(stored_ver)


def _response_version_fields(
    storage_stem: str, *, fallback_version: str | None = None
) -> Dict[str, Any]:
    """Build ``arxiv_version`` / ``versioned_id`` fields for tool responses."""
    bare = bare_arxiv_id(storage_stem)
    version = (
        _read_arxiv_version(storage_stem)
        or fallback_version
        or arxiv_version_suffix(storage_stem)
    )
    fields: Dict[str, Any] = {}
    if version:
        fields["arxiv_version"] = version
        fields["versioned_id"] = f"{bare}{version}"
    return fields


def _wants_force_refresh(arguments: Dict[str, Any]) -> bool:
    """Return True when the caller asked to overwrite a cached paper."""
    return bool(arguments.get("force") or arguments.get("refresh"))


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

download_tool = types.Tool(
    name="download_paper",
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
    description=(
        "Download a paper from arXiv and return its text content. "
        "Tries the HTML version first for clean extraction; falls back to "
        "PDF conversion if HTML is unavailable. Stores the paper locally. "
        "Returned text is bounded to roughly 12,000 characters by default so "
        "one call cannot return an unbounded paper body. When is_truncated is "
        "true, call again with start=next_start (see next_retrieval) to "
        "continue, or pass return_full_text=true for the entire remaining "
        "paper. Set force=true to re-fetch and overwrite a cached paper (required to replace a newer stored arXiv version with an older one)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "The arXiv ID of the paper to download (e.g. '2103.12345')",
            },
            "start": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Zero-based character offset for returning large papers in chunks; "
                    "pass next_start from a prior truncated response to continue"
                ),
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Maximum raw paper characters to return from start; "
                    "omit for the bounded default (12,000 chars)"
                ),
            },
            "return_full_text": {
                "type": "boolean",
                "description": (
                    "Set true to opt out of the bounded default and return the "
                    "entire remaining paper from start in one call"
                ),
            },
            "force": {
                "type": "boolean",
                "description": (
                    "If true, re-download and overwrite the local markdown and "
                    "metadata sidecar even if the paper is already cached, "
                    "including when replacing a newer stored arXiv version with "
                    "an older one. Default false."
                ),
            },
        },
        "required": ["paper_id"],
        "additionalProperties": False,
    },
)


# ---------------------------------------------------------------------------
# Core fetch functions (run synchronously, called via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _fetch_html_content(paper_id: str, deadline: float) -> str | None:
    """Try to get paper content from the arXiv HTML endpoint (sync wrapper).

    This function is called via asyncio.to_thread() from async code, so it runs
    in a worker thread. Rate limiter lock acquired per attempt, not held through sleeps.

    Args:
        paper_id: arXiv paper ID.
        deadline: Wall-clock deadline (time.monotonic()) for the entire download_paper operation.

    Returns:
        HTML text content on success, None if HTML is unavailable or budget exhausted.
    """
    import random

    # Retry loop is outside rate limiter so lock is released during sleeps
    last_exception: Exception | None = None

    def remaining_time() -> float:
        return max(0.0, deadline - time.monotonic())

    for attempt in range(settings.ARXIV_MAX_RETRIES + 1):
        # Check budget before attempt
        remaining = remaining_time()
        if remaining <= 0.1:
            logger.info(f"HTML fetch budget exhausted, will try PDF")
            return None

        try:
            # Cap this attempt's timeout to remaining budget
            attempt_timeout = min(
                float(settings.get_request_timeout()), remaining * 0.9
            )
            if attempt_timeout < 1.0:
                logger.info(f"HTML fetch insufficient budget for attempt, will try PDF")
                return None
            # Rate limiter only holds lock for this attempt
            # Check if we have enough time for the rate-limiter wait plus a minimal attempt
            pending_wait = ARXIV_RATE_LIMITER.seconds_until_next_slot()
            remaining_before_limiter = deadline - time.monotonic()
            min_attempt_time = 1.0  # Minimum time needed for the actual request
            if remaining_before_limiter < pending_wait + min_attempt_time:
                logger.info(
                    f"HTML fetch skipped: time budget (ARXIV_MAX_TOTAL_TIME) too small for another attempt "
                    f"(need {pending_wait + min_attempt_time:.1f}s, have {remaining_before_limiter:.1f}s), will try PDF"
                )
                return None
            return ARXIV_RATE_LIMITER.run_sync(
                lambda: _fetch_html_content_single_attempt(paper_id, attempt_timeout)
            )
        except ArxivRateLimitError:
            # Rate limit errors should propagate immediately
            raise
        except httpx.TimeoutException as e:
            last_exception = e
            if attempt < settings.ARXIV_MAX_RETRIES:
                wait = min(
                    settings.ARXIV_INITIAL_BACKOFF
                    * (2**attempt)
                    * (0.5 + random.random()),
                    settings.ARXIV_MAX_BACKOFF,
                )
                # Cap wait to remaining budget
                remaining = remaining_time()
                if wait >= remaining or remaining <= 0.1:
                    logger.info(
                        f"HTML fetch timeout: insufficient budget for retry, will try PDF"
                    )
                    return None
                wait = min(wait, remaining * 0.9)

                logger.warning(
                    "HTML fetch timed out; retrying in %.1fs (attempt %d/%d)",
                    wait,
                    attempt + 1,
                    settings.ARXIV_MAX_RETRIES + 1,
                )
                time.sleep(wait)
            else:
                logger.info(
                    f"HTML fetch timed out after {settings.ARXIV_MAX_RETRIES + 1} attempts, will try PDF"
                )
                return None
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            last_exception = e
            if attempt < settings.ARXIV_MAX_RETRIES:
                wait = min(
                    settings.ARXIV_INITIAL_BACKOFF
                    * (2**attempt)
                    * (0.5 + random.random()),
                    settings.ARXIV_MAX_BACKOFF,
                )
                # Cap wait to remaining budget
                remaining = remaining_time()
                if wait >= remaining or remaining <= 0.1:
                    logger.info(
                        f"HTML fetch connection error: insufficient budget for retry, will try PDF"
                    )
                    return None
                wait = min(wait, remaining * 0.9)

                logger.warning(
                    "HTML fetch connection error; retrying in %.1fs (attempt %d/%d)",
                    wait,
                    attempt + 1,
                    settings.ARXIV_MAX_RETRIES + 1,
                )
                time.sleep(wait)
            else:
                logger.info(
                    f"HTML connection failed after {settings.ARXIV_MAX_RETRIES + 1} attempts, will try PDF"
                )
                return None
        except httpx.RequestError as e:
            logger.warning(f"HTML fetch request error for {paper_id}: {e}")
            return None

    return None


def _fetch_html_content_single_attempt(
    paper_id: str, timeout_seconds: float
) -> str | None:
    """Single HTML fetch attempt (called with rate limiter lock held).

    Returns the extracted text on success, or None if the HTML endpoint
    is not available (404).

    Raises ArxivRateLimitError on 406/429/503 (406 uses minimal retry).
    Raises httpx.TimeoutException, httpx.ConnectError, etc on network errors.
    """
    url = f"https://arxiv.org/html/{paper_id}"
    timeout = httpx.Timeout(
        connect=float(settings.ARXIV_CONNECT_TIMEOUT),
        read=timeout_seconds,
        write=30.0,
        pool=30.0,
    )

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    if response.status_code == 200:
        logger.info(f"HTML fetch succeeded for {paper_id}")
        return _html_to_text(response.text)
    elif response.status_code == 404:
        logger.info(f"HTML not available for {paper_id}, will try PDF")
        return None
    elif response.status_code == 406:
        # HTTP 406 is IP-level burst throttling - return rate_limited, don't fall back to PDF (#277)
        from ..arxiv_api import _parse_retry_after_seconds
        from .search import _HTTP_406_RETRY_AFTER_SECONDS

        # Parse Retry-After header (supports numeric seconds and HTTP-date)
        retry_after_header = response.headers.get("Retry-After")
        retry_after = None
        if retry_after_header:
            retry_after = _parse_retry_after_seconds(retry_after_header)
        # Use 600s default only if no valid Retry-After header
        if retry_after is None:
            retry_after = _HTTP_406_RETRY_AFTER_SECONDS

        raise ArxivRateLimitError(
            f"arXiv is rate limiting this IP (HTTP 406). "
            f"Please wait {int(retry_after)} seconds before retrying.",
            status_code=406,
            retry_after_seconds=retry_after,
        )
    elif response.status_code in (429, 503):
        # HTML 429/503: fall back to PDF within single deadline (restore main's behavior)
        logger.info(
            f"HTML rate limited (HTTP {response.status_code}) for {paper_id}, will try PDF"
        )
        return None
    else:
        # Other status codes are not retryable
        logger.info(
            f"HTML fetch returned {response.status_code} for {paper_id}, will try PDF"
        )
        return None


class PaperNotFoundError(Exception):
    """Raised when an arXiv paper ID cannot be found."""


async def _paper_exists_on_arxiv(
    paper_id: str, deadline: float, overall_start: float
) -> bool:
    """Return True if arXiv has this paper/version.

    Uses the same Atom ``id_list`` lookup as ``get_abstract``, so a missing
    paper and a missing version both report as absent (empty feed).

    Args:
        paper_id: arXiv paper ID.
        deadline: Wall-clock deadline (time.monotonic()) for the entire download_paper operation.
        overall_start: Start time of the entire download_paper operation.
    """
    url = f"{ARXIV_API_URL}?id_list={paper_id}&max_results=1"

    # Use retry_with_backoff with the remaining budget from the shared deadline
    remaining = max(0.1, deadline - time.monotonic())

    try:
        async with httpx.AsyncClient(
            timeout=float(settings.get_request_timeout())
        ) as client:
            response = await retry_with_backoff(
                lambda: _rate_limited_get(client, url),
                max_retries=settings.ARXIV_MAX_RETRIES,
                initial_backoff=settings.ARXIV_INITIAL_BACKOFF,
                max_backoff=settings.ARXIV_MAX_BACKOFF,
                max_total_time=remaining,
                operation_name="existence check",
            )
    except ArxivTimeoutError:
        # Re-raise with overall elapsed time instead of just the existence check budget
        overall_elapsed = time.monotonic() - overall_start
        overall_budget = deadline - overall_start
        raise ArxivTimeoutError(
            f"Paper metadata lookup timed out after {overall_elapsed:.0f}s "
            f"(total budget: {overall_budget:.0f}s). "
            f"The arXiv API may be slow or overloaded. Please retry shortly."
        )
    root = ET.fromstring(response.text)
    return bool(root.findall("atom:entry", ARXIV_NS))


def _download_arxiv_pdf_to_path(
    paper: arxiv.Result, pdf_path: Path, deadline: float
) -> None:
    """Persist an arXiv PDF using the version-independent streaming helper.

    Args:
        paper: arXiv result with PDF URL.
        pdf_path: Path where the PDF will be saved.
        deadline: Wall-clock deadline (time.monotonic()) for the entire download_paper operation.
    """
    stream_pdf_to_path(
        paper,
        pdf_path,
        request_timeout=float(settings.get_request_timeout()),
        user_agent=(
            f"{settings.APP_NAME}/{settings.APP_VERSION} "
            "(https://github.com/blazickjp/arxiv-mcp-server; research tool)"
        ),
        deadline=deadline,
    )


def _fetch_pdf_content_unlocked(
    paper_id: str, deadline: float
) -> tuple[str, arxiv.Result]:
    """Download the PDF from arXiv and convert it to Markdown synchronously.

    The PDF bytes are fetched with :func:`_download_arxiv_pdf_to_path` rather
    than ``arxiv.Result.download_pdf()`` to avoid truncated downloads on
    ``export.arxiv.org`` for some files.

    Args:
        paper_id: arXiv paper ID.
        deadline: Wall-clock deadline (time.monotonic()) for the entire download_paper operation.

    Returns:
        (markdown_text, arxiv_result).

    Raises:
        PaperNotFoundError if the paper does not exist, or other exceptions
        on network/conversion failures.
        ImportError (with a helpful message) if the [pdf] extra is not installed.
        ArxivRateLimitError on 406/429/503 (406 uses minimal retry per #277).
        httpx.HTTPStatusError on other HTTP errors (cleaned by caller).
    """
    if not _load_pdf_dependencies():
        raise ImportError(
            "PDF conversion requires the pdf extra: "
            "pip install arxiv-mcp-server[pdf]"
        )

    # Use a client with minimal retries for the metadata lookup to avoid
    # making many requests on 406/429/503 (issue #277: 406 is IP-level throttling).
    # Note: Retry-After headers cannot be honored on this path because the
    # arxiv package's HTTPError does not preserve response headers.

    # Check deadline before metadata lookup
    pending_wait = ARXIV_RATE_LIMITER.seconds_until_next_slot()
    remaining = deadline - time.monotonic()
    min_attempt_time = 1.0  # Minimum time needed for metadata lookup
    if remaining < pending_wait + min_attempt_time:
        raise ArxivTimeoutError(
            f"Paper metadata lookup skipped: time budget (ARXIV_MAX_TOTAL_TIME) too small for another attempt "
            f"(need {pending_wait + min_attempt_time:.1f}s, have {remaining:.1f}s)"
        )

    client = get_arxiv_client(num_retries=0)
    try:
        paper = ARXIV_RATE_LIMITER.run_sync(
            lambda: next(client.results(arxiv.Search(id_list=[paper_id])))
        )
    except StopIteration:
        raise PaperNotFoundError(f"Paper {paper_id} not found on arXiv")
    except arxiv.HTTPError as e:
        # arxiv.HTTPError has a status attribute
        status = e.status
        # Create httpx.HTTPStatusError for consistent handling by caller
        request = httpx.Request("GET", "(arXiv metadata)")
        response = httpx.Response(status, request=request)
        raise httpx.HTTPStatusError(
            "arXiv metadata request failed",
            request=request,
            response=response,
        )
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    ):
        # Network errors: report cleanly without URL or traceback
        raise RuntimeError("Could not reach arXiv (network error)") from None
    finally:
        # Close the per-call session created by the num_retries=0 client
        session = getattr(client, "_session", None)
        if session and hasattr(session, "close"):
            session.close()

    pdf_path = get_paper_path(paper_id, ".pdf")
    _download_arxiv_pdf_to_path(paper, pdf_path, deadline)

    try:
        logger.info(f"Converting PDF to markdown for {paper_id}")
        markdown = pymupdf4llm.to_markdown(pdf_path, show_progress=False)
        return markdown, paper
    finally:
        # Release pymupdf C-level memory and never retain temporary PDFs,
        # including when conversion raises midway through processing.
        gc.collect()
        try:
            pdf_path.unlink()
        except OSError:
            pass


def _fetch_pdf_content(paper_id: str, deadline: float) -> tuple[str, arxiv.Result]:
    """Serialize download and conversion for requests targeting the same paper.

    Args:
        paper_id: arXiv paper ID.
        deadline: Wall-clock deadline (time.monotonic()) for the entire download_paper operation.
    """
    lock = _pdf_conversion_locks[hash(paper_id) % len(_pdf_conversion_locks)]
    with lock:
        return _fetch_pdf_content_unlocked(paper_id, deadline)


def _metadata_from_arxiv_result(paper_id: str, paper) -> dict[str, Any]:
    """Build local list_papers metadata from an arXiv result."""
    published = getattr(paper, "published", None)
    published_text = None
    if published is not None:
        iso = getattr(published, "isoformat", None)
        published_text = iso() if callable(iso) else str(published)
    authors = []
    for author in getattr(paper, "authors", None) or []:
        name = getattr(author, "name", None)
        if name:
            authors.append(name)
        elif author:
            authors.append(str(author))
    title = getattr(paper, "title", None) or None
    if isinstance(title, str):
        title = " ".join(title.split()) or None
    return {
        "title": title,
        "authors": authors,
        "published": published_text,
        "arxiv_version": _version_from_arxiv_result(paper),
    }


def _fetch_arxiv_metadata(
    paper_id: str, deadline: float | None = None
) -> dict[str, Any] | None:
    """Best-effort arXiv metadata lookup used after an HTML download.

    Makes a direct GET to the arXiv API with streaming and per-chunk deadline
    checks to handle slow trickle. Metadata is optional: on deadline or error,
    returns None without failing the download.

    Args:
        paper_id: arXiv paper ID.
        deadline: Optional wall-clock deadline (time.monotonic()). When provided,
                  enforces the remaining budget across gate acquisition, request
                  connect, and streaming read with per-chunk deadline checks.
    """
    import requests
    import feedparser

    try:
        # Check if we have enough time budget remaining before attempting gate acquisition.
        # The pre-gate check uses seconds_until_next_slot() which is advisory (unlocked),
        # so the actual gate wait could be longer if another request starts. Worst case:
        # another request acquires the gate just after our check, so we wait up to
        # min_interval (3s). The post-gate recheck below skips if that causes overrun.
        if deadline is not None:
            pending_wait = ARXIV_RATE_LIMITER.seconds_until_next_slot()
            remaining = deadline - time.monotonic()
            min_attempt_time = 1.0
            if remaining < pending_wait + min_attempt_time:
                logger.info(
                    "Metadata lookup skipped: insufficient time budget "
                    f"(need {pending_wait + min_attempt_time:.1f}s, have {remaining:.1f}s)"
                )
                return None

        # Make direct GET to arXiv API inside the rate limiter gate.
        # No retries: exactly 1 request. On 406, return None (issue #277: IP throttling).
        def fetch_with_streaming_deadline_check():
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining < 1.0:
                    logger.info(
                        f"Metadata lookup skipped: deadline exceeded after gate wait "
                        f"(remaining: {remaining:.1f}s)"
                    )
                    return None

            # Direct GET to export API with streaming and clamped timeouts
            url = f"https://export.arxiv.org/api/query?id_list={paper_id}"
            timeout_tuple = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining < 0.5:
                    logger.info("Metadata lookup skipped: deadline before request")
                    return None
                # Clamp connect and read timeouts to remaining budget
                timeout_tuple = (
                    min(float(settings.ARXIV_CONNECT_TIMEOUT), remaining),
                    min(float(settings.get_request_timeout()), remaining),
                )
            else:
                timeout_tuple = (
                    float(settings.ARXIV_CONNECT_TIMEOUT),
                    float(settings.get_request_timeout()),
                )

            try:
                response = requests.get(url, stream=True, timeout=timeout_tuple)

                # On 406, return None immediately (issue #277: IP throttling, no retry)
                if response.status_code == 406:
                    logger.info(f"Metadata lookup rate limited (406) for {paper_id}")
                    response.close()
                    return None

                response.raise_for_status()

                # Stream response body with per-chunk deadline checks
                # (requests read timeout is max gap between chunks, not total time)
                chunks = []
                try:
                    for chunk in response.iter_content(
                        chunk_size=8192, decode_unicode=False
                    ):
                        if deadline is not None and time.monotonic() >= deadline:
                            logger.info(
                                f"Metadata lookup timed out (slow trickle) for {paper_id}"
                            )
                            response.close()
                            return None
                        if chunk:
                            chunks.append(chunk)
                finally:
                    response.close()

                # Parse feed with feedparser (already a dependency of arxiv package)
                feed_text = b"".join(chunks).decode("utf-8", errors="replace")
                feed = feedparser.parse(feed_text)

                if not feed.entries:
                    logger.info(f"No metadata entries found for {paper_id}")
                    return None

                # Build metadata dict matching _metadata_from_arxiv_result format
                entry = feed.entries[0]
                return {
                    "title": entry.get("title", "").strip(),
                    "authors": [
                        author.get("name", "").strip()
                        for author in entry.get("authors", [])
                    ],
                    "summary": entry.get("summary", "").strip(),
                    "published": entry.get("published", ""),
                    "updated": entry.get("updated", ""),
                    "primary_category": (
                        entry.get("arxiv_primary_category", {}).get("term", "")
                    ),
                    "categories": [
                        tag.get("term", "")
                        for tag in entry.get("tags", [])
                        if tag.get("term")
                    ],
                    "arxiv_url": entry.get("id", ""),
                }
            except requests.exceptions.Timeout:
                logger.info(f"Metadata lookup timed out for {paper_id}")
                return None
            except requests.exceptions.RequestException as exc:
                logger.info(f"Metadata request failed for {paper_id}: {exc}")
                return None

        metadata = ARXIV_RATE_LIMITER.run_sync(fetch_with_streaming_deadline_check)
        return metadata
    except Exception as exc:
        logger.info("Could not fetch metadata for %s: %s", paper_id, exc)
        return None


def _persist_paper_metadata(
    paper_id: str,
    arxiv_result=None,
    title_hint: str | None = None,
    arxiv_version: str | None = None,
    deadline: float | None = None,
) -> None:
    """Write a sidecar from arXiv API metadata. Never fail the download.

    Args:
        paper_id: arXiv paper ID.
        arxiv_result: Optional arXiv result object with metadata.
        title_hint: Optional title hint (unused).
        arxiv_version: Optional version string.
        deadline: Optional wall-clock deadline (time.monotonic()) for metadata lookup.
    """
    try:
        metadata = None
        if arxiv_result is not None:
            metadata = _metadata_from_arxiv_result(paper_id, arxiv_result)
        if metadata is None:
            metadata = _fetch_arxiv_metadata(paper_id, deadline)
        if metadata is None:
            # Prefer null fields over HTML-scraped / truncated titles.
            metadata = {
                "title": None,
                "authors": [],
                "published": None,
            }
        version = (
            arxiv_version
            or (metadata.get("arxiv_version") if metadata else None)
            or arxiv_version_suffix(paper_id)
        )
        save_paper_metadata(
            paper_id,
            title=metadata.get("title") or None,
            authors=metadata.get("authors") or [],
            published=metadata.get("published"),
            extractor_version=EXTRACTOR_VERSION,
            arxiv_version=version,
            path=get_paper_path(paper_id, ".meta.json"),
        )
    except Exception:
        logger.warning("Failed to persist metadata for %s", paper_id, exc_info=True)


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def handle_download(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle paper download requests synchronously (HTML first, then PDF).

    Uses a single deadline for the entire operation (HTML + existence check + PDF)
    to ensure the total time stays within ARXIV_MAX_TOTAL_TIME.
    """
    # Create single deadline for entire operation
    start_time = time.monotonic()
    max_total_time = float(settings.ARXIV_MAX_TOTAL_TIME)
    deadline = start_time + max_total_time

    try:
        raw_id = arguments["paper_id"]
        paper_id = parse_arxiv_id(raw_id) if isinstance(raw_id, str) else None
        if paper_id is None:
            display = raw_id.strip() if isinstance(raw_id, str) else raw_id
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": f"Invalid arXiv ID: {display}",
                        }
                    ),
                )
            ]
        # Fetch may use a versioned ID; storage always uses the bare key so
        # read_paper("1706.03762") finds download_paper("1706.03762v7") (#202).
        storage_id = bare_arxiv_id(paper_id)
        requested_version = arxiv_version_suffix(paper_id)
        md_path = get_paper_path(storage_id, ".md")
        force = _wants_force_refresh(arguments)

        # --- Cache hit: bare key (via get_paper_path) or legacy alias ---
        if not force:
            resolved = None
            if md_path.exists() and _cache_satisfies_request(storage_id, paper_id):
                resolved = storage_id
            else:
                # Legacy versioned filenames still live under STORAGE_PATH.
                legacy = resolve_stored_stem(paper_id, Path(settings.STORAGE_PATH))
                if (
                    legacy
                    and legacy != storage_id
                    and _cache_satisfies_request(legacy, paper_id)
                ):
                    resolved = legacy
            if resolved:
                content = get_paper_path(resolved, ".md").read_text(encoding="utf-8")
                cache_payload = {
                    "status": "success",
                    "message": "Paper already available (returned from cache)",
                    "paper_id": storage_id,
                    "source": "cache",
                }
                cache_payload.update(_response_version_fields(resolved))
                payload = add_content_payload(
                    cache_payload,
                    content,
                    arguments,
                    CONTENT_WARNING,
                )
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(payload),
                    )
                ]

            # Bare-ID storage: refuse silent downgrade of a newer cached version
            # (#206). Keep the newer content and return a clear cache status.
            if md_path.exists() and _would_downgrade_cached_version(
                storage_id, paper_id
            ):
                content = md_path.read_text(encoding="utf-8")
                stored_ver = _read_arxiv_version(storage_id)
                refuse_payload = {
                    "status": "success",
                    "message": (
                        f"Kept newer cached version {stored_ver}; refused to "
                        f"overwrite with older requested version "
                        f"{requested_version} without force=true"
                    ),
                    "paper_id": storage_id,
                    "source": "cache",
                    "downgrade_refused": True,
                    "requested_version": requested_version,
                }
                refuse_payload.update(_response_version_fields(storage_id))
                payload = add_content_payload(
                    refuse_payload,
                    content,
                    arguments,
                    CONTENT_WARNING,
                )
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(payload),
                    )
                ]

        # --- Try HTML endpoint first ---
        html_text = await asyncio.to_thread(_fetch_html_content, paper_id, deadline)

        if html_text is not None:
            # Save to cache under the bare ID
            md_path.write_text(html_text, encoding="utf-8")
            await asyncio.to_thread(
                _persist_paper_metadata,
                storage_id,
                None,
                None,
                requested_version,
                deadline,
            )
            _cleanup_versioned_aliases(storage_id)
            # Best-effort index; the tracked task is drained at shutdown.
            _track_index_task(_run_index_by_id(storage_id))
            html_payload = {
                "status": "success",
                "message": "Paper fetched from arXiv HTML endpoint",
                "paper_id": storage_id,
                "source": "html",
            }
            html_payload.update(
                _response_version_fields(storage_id, fallback_version=requested_version)
            )
            payload = add_content_payload(
                html_payload,
                html_text,
                arguments,
                CONTENT_WARNING,
            )
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(payload),
                )
            ]

        # --- HTML not available: fall back to PDF ---
        # Distinguish a missing paper/version from a missing [pdf] extra so
        # callers are not told to pip-install when the ID simply does not exist
        # (issue #196). Same Atom id_list check as get_abstract.
        if not await _paper_exists_on_arxiv(paper_id, deadline, start_time):
            raise PaperNotFoundError(f"Paper {paper_id} not found on arXiv")

        if not _load_pdf_dependencies():
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": (
                                "HTML version not available and PDF conversion "
                                "requires the pdf extra: "
                                "pip install arxiv-mcp-server[pdf]"
                            ),
                        }
                    ),
                )
            ]

        logger.info(f"Falling back to PDF download for {paper_id}")
        markdown, arxiv_result = await asyncio.to_thread(
            _fetch_pdf_content, paper_id, deadline
        )

        # Save to cache under the bare ID
        md_path.write_text(markdown, encoding="utf-8")
        await asyncio.to_thread(
            _persist_paper_metadata,
            storage_id,
            arxiv_result,
            None,
            requested_version,
            deadline,
        )
        _cleanup_versioned_aliases(storage_id)

        # Best-effort index; the tracked task is drained at shutdown.
        _track_index_task(_run_index_from_result(arxiv_result))

        pdf_payload = {
            "status": "success",
            "message": "Paper fetched via PDF conversion",
            "paper_id": storage_id,
            "source": "pdf",
        }
        pdf_payload.update(
            _response_version_fields(
                storage_id,
                fallback_version=requested_version
                or _version_from_arxiv_result(arxiv_result),
            )
        )
        payload = add_content_payload(
            pdf_payload,
            markdown,
            arguments,
            CONTENT_WARNING,
        )
        return [
            types.TextContent(
                type="text",
                text=json.dumps(payload),
            )
        ]

    except ArxivRateLimitError as e:
        # Rate limit from _rate_limited_get or stream_pdf_to_path (issue #277)
        return _rate_limited_response(
            str(e),
            retry_after_seconds=e.retry_after_seconds,
            status_code=e.status_code,
        )
    except httpx.HTTPStatusError as e:
        # HTTP errors from _rate_limited_get (existence check) or other HTTP calls (#166, #277)
        status = e.response.status_code if e.response is not None else "unknown"
        # Handle 406 as rate limiting
        if status == 406:
            from ..arxiv_api import _parse_retry_after_seconds
            from .search import _HTTP_406_RETRY_AFTER_SECONDS

            # Parse Retry-After header (supports numeric seconds and HTTP-date)
            retry_after = None
            if e.response is not None:
                retry_after_header = e.response.headers.get("Retry-After")
                if retry_after_header:
                    retry_after = _parse_retry_after_seconds(retry_after_header)
            # Use 600s default only if no valid Retry-After header
            if retry_after is None:
                retry_after = _HTTP_406_RETRY_AFTER_SECONDS

            message = (
                f"arXiv is rate limiting this IP (HTTP 406). "
                f"Please wait {int(retry_after)} seconds before retrying."
            )
            return _rate_limited_response(
                message,
                retry_after_seconds=retry_after,
                status_code=406,
            )
        # Handle 429/503 as rate limiting
        elif status in (429, 503):
            # Parse Retry-After header
            retry_after = None
            if e.response is not None:
                retry_after_header = e.response.headers.get("Retry-After")
                if retry_after_header:
                    try:
                        retry_after = float(retry_after_header)
                    except ValueError:
                        pass
            if retry_after is None:
                from .search import _DEFAULT_RETRY_AFTER_SECONDS

                retry_after = _DEFAULT_RETRY_AFTER_SECONDS

            message = (
                f"arXiv is rate limiting this IP (HTTP {status}). "
                f"Please wait {int(retry_after)} seconds before retrying."
            )
            return _rate_limited_response(
                message,
                retry_after_seconds=retry_after,
                status_code=status,
            )
        # Other HTTP errors
        logger.error("HTTP error downloading paper: %s", status)
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "message": f"arXiv API HTTP error (HTTP {status})",
                    }
                ),
            )
        ]
    except PaperNotFoundError as e:
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "message": str(e),
                    }
                ),
            )
        ]
    except OSError:
        # Never leak absolute host paths from filesystem errors to the client.
        safe_id = locals().get("storage_id") or locals().get("paper_id") or "unknown"
        logger.exception("Storage error downloading %s", safe_id)
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "message": f"Storage error while saving paper {safe_id}",
                    }
                ),
            )
        ]
    except ArxivTimeoutError as e:
        # Budget exhaustion from HTML, PDF, or existence check
        safe_id = locals().get("paper_id") or "unknown"
        message = str(e)
        logger.error("Timeout downloading %s: %s", safe_id, message)
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "message": message,
                    }
                ),
            )
        ]
    except RuntimeError as e:
        # Clean HTTP errors from stream_pdf_to_path (issue #166)
        safe_id = locals().get("paper_id") or "unknown"
        message = str(e)
        # Only log traceback if it's not a known HTTP or network error
        if "HTTP error" in message or "network error" in message:
            logger.error("Download error for %s: %s", safe_id, message)
        else:
            logger.exception("Unexpected runtime error downloading %s", safe_id)
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"status": "error", "message": message}),
            )
        ]
    except Exception as e:
        safe_id = locals().get("paper_id") or "unknown"
        logger.exception("Unexpected error downloading %s", safe_id)
        message = str(e)
        try:
            storage_root = str(Path(settings.STORAGE_PATH))
            if storage_root and storage_root in message:
                message = message.replace(storage_root, "<storage>")
        except Exception:
            pass
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"status": "error", "message": f"Error: {message}"}),
            )
        ]
