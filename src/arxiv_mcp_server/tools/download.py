"""Download functionality for the arXiv MCP server."""

import arxiv
import gc
import json
import asyncio
import httpx
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Any, List
import mcp.types as types
from mcp.types import ToolAnnotations
from ..config import Settings, get_arxiv_client
import logging

_MAX_TRACKED_CONVERSIONS = 100  # prevent unbounded growth of conversion_statuses

# Optional PDF-conversion dependencies — only needed for the PDF fallback path.
# Install with: pip install arxiv-mcp-server[pdf]
try:
    import pymupdf4llm
    import fitz

    _pdf_available = True
except ImportError:  # pragma: no cover
    pymupdf4llm = None  # type: ignore[assignment]
    fitz = None  # type: ignore[assignment]
    _pdf_available = False

# Optional pro feature — gracefully degrade when not installed
try:
    from .semantic_search import index_paper_by_id, index_paper_from_result

    _semantic_search_available = True
except ImportError:  # pragma: no cover
    _semantic_search_available = False
    index_paper_by_id = None  # type: ignore[assignment]
    index_paper_from_result = None  # type: ignore[assignment]

logger = logging.getLogger("arxiv-mcp-server")

# Serialise background indexing to avoid hammering the GPU/CPU when multiple
# papers are downloaded in parallel (issue #68).
_index_semaphore: asyncio.Semaphore | None = None


def _get_index_semaphore() -> asyncio.Semaphore:
    """Return the module-level indexing semaphore, creating it lazily."""
    global _index_semaphore
    if _index_semaphore is None:
        _index_semaphore = asyncio.Semaphore(1)
    return _index_semaphore


async def _run_index_by_id(paper_id: str) -> None:
    """Acquire the index semaphore then run index_paper_by_id in a thread."""
    if not _semantic_search_available:
        return
    async with _get_index_semaphore():
        await asyncio.to_thread(index_paper_by_id, paper_id)


async def _run_index_from_result(arxiv_result) -> None:
    """Acquire the index semaphore then run index_paper_from_result in a thread."""
    if not _semantic_search_available:
        return
    async with _get_index_semaphore():
        await asyncio.to_thread(index_paper_from_result, arxiv_result)


settings = Settings()

if _pdf_available:
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


class _ArticleTextExtractor(HTMLParser):
    """Extract readable text from an arXiv HTML paper page.

    Strategy:
      - Ignore content inside <script>, <style>, <nav>, <header>, <footer> tags.
      - Collect text from everywhere else, with minimal whitespace cleanup.
    """

    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside"}

    def __init__(self):
        super().__init__()
        self._skip_depth: int = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._chunks)


def _html_to_text(html: str) -> str:
    """Parse raw HTML and return cleaned plain text."""
    parser = _ArticleTextExtractor()
    parser.feed(html)
    return parser.get_text()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_paper_path(paper_id: str, suffix: str = ".md") -> Path:
    """Get the absolute file path for a paper with given suffix."""
    storage_path = Path(settings.STORAGE_PATH)
    storage_path.mkdir(parents=True, exist_ok=True)
    return storage_path / f"{paper_id}{suffix}"


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

download_tool = types.Tool(
    name="download_paper",
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
    description=(
        "Download a paper from arXiv and return its full text content. "
        "Tries the HTML version first for clean extraction; falls back to "
        "PDF conversion if HTML is unavailable. Returns the paper content "
        "directly so you can read it immediately."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "The arXiv ID of the paper to download (e.g. '2103.12345')",
            },
        },
        "required": ["paper_id"],
    },
)


# ---------------------------------------------------------------------------
# Core fetch functions (run synchronously, called via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _fetch_html_content(paper_id: str) -> str | None:
    """Try to get paper content from the arXiv HTML endpoint.

    Returns the extracted text on success, or None if the HTML endpoint
    is not available (404 or other non-200 status).
    """
    url = f"https://arxiv.org/html/{paper_id}"
    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        if response.status_code == 200:
            logger.info(f"HTML fetch succeeded for {paper_id}")
            return _html_to_text(response.text)
        logger.info(
            f"HTML fetch returned {response.status_code} for {paper_id}, will try PDF"
        )
        return None
    except httpx.RequestError as exc:
        logger.warning(f"HTML fetch request error for {paper_id}: {exc}")
        return None


class PaperNotFoundError(Exception):
    """Raised when an arXiv paper ID cannot be found."""


def _fetch_pdf_content(paper_id: str) -> tuple[str, arxiv.Result]:
    """Download the PDF from arXiv and convert it to Markdown synchronously.

    Returns (markdown_text, arxiv_result).
    Raises PaperNotFoundError if the paper does not exist, or other exceptions
    on network/conversion failures.
    Raises ImportError (with a helpful message) if the [pdf] extra is not installed.
    """
    if not _pdf_available:
        raise ImportError(
            "PDF conversion requires the pdf extra: "
            "pip install arxiv-mcp-server[pdf]"
        )

    client = get_arxiv_client()
    try:
        paper = next(client.results(arxiv.Search(id_list=[paper_id])))
    except StopIteration:
        raise PaperNotFoundError(f"Paper {paper_id} not found on arXiv")

    pdf_path = get_paper_path(paper_id, ".pdf")
    paper.download_pdf(dirpath=pdf_path.parent, filename=pdf_path.name)

    logger.info(f"Converting PDF to markdown for {paper_id}")
    markdown = pymupdf4llm.to_markdown(pdf_path, show_progress=False)

    # Release pymupdf C-level memory and clean up PDF
    gc.collect()
    # Clean up the PDF — we only keep the markdown
    try:
        pdf_path.unlink()
    except OSError:
        pass

    return markdown, paper


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def handle_download(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle paper download requests synchronously (HTML first, then PDF)."""
    try:
        paper_id = arguments["paper_id"]
        md_path = get_paper_path(paper_id, ".md")

        # --- Cache hit: return immediately with content ---
        if md_path.exists():
            content = md_path.read_text(encoding="utf-8")
            # Best-effort background index refresh (serialised via semaphore)
            try:
                asyncio.create_task(_run_index_by_id(paper_id))
            except RuntimeError:
                pass
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "success",
                            "message": "Paper already available (returned from cache)",
                            "paper_id": paper_id,
                            "source": "cache",
                            "content": content,
                        }
                    ),
                )
            ]

        # --- Try HTML endpoint first ---
        html_text = await asyncio.to_thread(_fetch_html_content, paper_id)

        if html_text is not None:
            # Save to cache
            md_path.write_text(html_text, encoding="utf-8")
            # Best-effort index (serialised via semaphore)
            try:
                asyncio.create_task(_run_index_by_id(paper_id))
            except RuntimeError:
                pass
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "success",
                            "message": "Paper fetched from arXiv HTML endpoint",
                            "paper_id": paper_id,
                            "source": "html",
                            "content": html_text,
                        }
                    ),
                )
            ]

        # --- HTML not available: fall back to PDF ---
        if not _pdf_available:
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
        markdown, arxiv_result = await asyncio.to_thread(_fetch_pdf_content, paper_id)

        # Save to cache
        md_path.write_text(markdown, encoding="utf-8")

        # Best-effort index (serialised via semaphore)
        try:
            asyncio.create_task(_run_index_from_result(arxiv_result))
        except RuntimeError:
            pass

        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "message": "Paper fetched via PDF conversion",
                        "paper_id": paper_id,
                        "source": "pdf",
                        "content": markdown,
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
    except Exception as e:
        logger.exception(f"Unexpected error downloading {paper_id}")
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"status": "error", "message": f"Error: {str(e)}"}),
            )
        ]
