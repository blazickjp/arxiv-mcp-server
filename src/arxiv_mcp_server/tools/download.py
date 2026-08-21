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
from ..arxiv_api import ARXIV_RATE_LIMITER, stream_pdf_to_path
from .content import add_content_payload
from .list_papers import is_valid_arxiv_id, save_paper_metadata
import logging
import threading

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


logger = logging.getLogger("albert-mcp-server")
