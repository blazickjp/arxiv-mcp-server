"""PDF download functionality for the arXiv MCP server."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import httpx
import mcp.types as types
from mcp.types import ToolAnnotations

from ..config import Settings
from .arxiv_rate_limit import run_sync_with_arxiv_slot

import logging

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

_ARXIV_ID_RE = re.compile(
    r"^(\d{4}\.\d{4,5}(v\d+)?"  # new-style: 2404.18922 or 2404.18922v3
    # old-style: hep-ph/9901234, math.AG/0601001, cs.CV/0101011
    r"|[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)?/\d{7}(v\d+)?)$",
    re.IGNORECASE,
)


class PaperDownloadError(Exception):
    """Raised when an arXiv PDF cannot be downloaded or validated."""


def _safe_default_filename(paper_id: str) -> str:
    """Return a filesystem-safe default PDF filename for an arXiv ID."""
    return paper_id.replace("/", "_") + ".pdf"


def _normalize_filename(filename: str | None, paper_id: str) -> str:
    """Return a local PDF filename, rejecting path-like values."""
    if filename is not None and not isinstance(filename, str):
        raise ValueError("filename must be a string")

    candidate = filename.strip() if filename else _safe_default_filename(paper_id)
    if not candidate:
        candidate = _safe_default_filename(paper_id)

    path = Path(candidate)
    if path.name != candidate:
        raise ValueError("filename must be a file name only, not a path")
    if path.suffix.lower() != ".pdf":
        candidate = f"{candidate}.pdf"
    return candidate


def _resolve_output_path(
    paper_id: str,
    *,
    output_dir: str | None = None,
    filename: str | None = None,
) -> Path:
    """Resolve the final local PDF path and ensure its directory exists."""
    directory = (
        Path(output_dir).expanduser() if output_dir else Path(settings.STORAGE_PATH)
    )
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / _normalize_filename(filename, paper_id)


def _arxiv_pdf_url(paper_id: str) -> str:
    """Build the canonical arXiv PDF URL for a paper ID."""
    return f"https://arxiv.org/pdf/{paper_id}"


def _download_arxiv_pdf_to_path(paper_id: str, pdf_path: Path) -> str:
    """Download an arXiv PDF directly to ``pdf_path``.

    The file is written to a temporary sibling path first, validated by its PDF
    header, then atomically moved into place.
    """
    if not _ARXIV_ID_RE.match(paper_id):
        raise PaperDownloadError(f"Invalid arXiv paper ID: {paper_id}")

    pdf_url = _arxiv_pdf_url(paper_id)
    tmp_path = pdf_path.with_name(f".{pdf_path.name}.tmp")
    read_timeout = max(120.0, float(settings.REQUEST_TIMEOUT))
    timeout = httpx.Timeout(
        connect=30.0,
        read=read_timeout,
        write=30.0,
        pool=30.0,
    )
    headers = {
        "User-Agent": (
            f"{settings.APP_NAME}/{settings.APP_VERSION} "
            "(https://github.com/blazickjp/arxiv-mcp-server; research tool)"
        ),
    }

    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers=headers
        ) as client:
            with client.stream("GET", pdf_url) as response:
                response.raise_for_status()
                with tmp_path.open("wb") as out:
                    for chunk in response.iter_bytes(chunk_size=256 * 1024):
                        if chunk:
                            out.write(chunk)

        with tmp_path.open("rb") as downloaded:
            is_pdf = downloaded.read(5) == b"%PDF-"
        if not is_pdf:
            bad_path = tmp_path.with_suffix(".bad")
            tmp_path.replace(bad_path)
            raise PaperDownloadError(f"Downloaded file is not a valid PDF: {bad_path}")

        tmp_path.replace(pdf_path)
        return pdf_url
    except httpx.HTTPStatusError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        if exc.response.status_code == 404:
            raise PaperDownloadError(f"Paper {paper_id} not found on arXiv") from exc
        raise PaperDownloadError(
            f"arXiv returned HTTP {exc.response.status_code} for {paper_id}"
        ) from exc
    except httpx.RequestError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise PaperDownloadError(f"Failed to download {paper_id}: {exc}") from exc


download_tool = types.Tool(
    name="download_paper",
    annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
    description=(
        "Download a paper PDF directly from arXiv and store it locally. "
        "This tool does not parse or convert the PDF content. "
        "Optionally provide output_dir and filename to control where the PDF is saved."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "The arXiv ID of the paper to download (e.g. '2103.12345')",
            },
            "output_dir": {
                "type": "string",
                "description": "Optional directory to save the PDF. Defaults to the server storage path.",
            },
            "filename": {
                "type": "string",
                "description": "Optional PDF file name. '.pdf' is appended if omitted.",
            },
        },
        "required": ["paper_id"],
        "additionalProperties": False,
    },
)


async def handle_download(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle PDF download requests."""
    paper_id = arguments.get("paper_id")
    try:
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise PaperDownloadError("paper_id is required")
        paper_id = paper_id.strip()

        output_path = _resolve_output_path(
            paper_id,
            output_dir=arguments.get("output_dir"),
            filename=arguments.get("filename"),
        )
        pdf_url = await run_sync_with_arxiv_slot(
            lambda: _download_arxiv_pdf_to_path(paper_id, output_path)
        )

        payload = {
            "status": "success",
            "message": "PDF downloaded from arXiv",
            "paper_id": paper_id,
            "pdf_url": pdf_url,
            "path": str(output_path),
            "filename": output_path.name,
            "size_bytes": output_path.stat().st_size,
        }
        return [types.TextContent(type="text", text=json.dumps(payload))]
    except PaperDownloadError as exc:
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"status": "error", "message": str(exc)}),
            )
        ]
    except Exception as exc:
        logger.exception("Unexpected error downloading %s", paper_id)
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"status": "error", "message": f"Error: {exc}"}),
            )
        ]
