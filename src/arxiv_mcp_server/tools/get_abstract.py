"""Get paper abstract/metadata without downloading the full paper."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types
from mcp.types import ToolAnnotations

from .arxiv_ids import parse_arxiv_id
from .content import CONTENT_WARNING
from .search import (
    _rate_limited_get,
    ARXIV_API_URL,
    _rate_limited_response,
)
from ..arxiv_api import ArxivRateLimitError
from ..config import Settings
import httpx
import xml.etree.ElementTree as ET

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

abstract_tool = types.Tool(
    name="get_abstract",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Fetch abstract and metadata by arXiv ID without downloading the paper. "
        "Use before download_paper to assess relevance. Returns title, authors, "
        "abstract, categories, published date, and PDF URL. After compact search, "
        "use for one full abstract; skip if search used abstract_mode=full."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "The arXiv paper ID (e.g. '2401.12345' or '2404.19756')",
            }
        },
        "required": ["paper_id"],
        "additionalProperties": False,
    },
)


async def handle_get_abstract(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Fetch paper metadata via arXiv API without downloading the full paper."""
    try:
        raw_id = arguments["paper_id"]
        paper_id = raw_id.strip() if isinstance(raw_id, str) else ""
        if not paper_id:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "error", "message": "paper_id is required"}
                    ),
                )
            ]

        parsed = parse_arxiv_id(paper_id)
        if parsed is None:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "error", "message": "invalid arXiv ID format"}
                    ),
                )
            ]
        paper_id = parsed

        url = f"{ARXIV_API_URL}?id_list={paper_id}&max_results=1"

        timeout = httpx.Timeout(
            connect=float(settings.ARXIV_CONNECT_TIMEOUT),
            read=float(settings.ARXIV_REQUEST_TIMEOUT),
            write=30.0,
            pool=30.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await _rate_limited_get(client, url)

        root = ET.fromstring(response.text)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        entries = root.findall("atom:entry", ns)
        if not entries:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": f"Paper {paper_id} not found on arXiv",
                        }
                    ),
                )
            ]

        entry = entries[0]

        def text(tag: str) -> str:
            el = entry.find(tag, ns)
            return (el.text or "").strip().replace("\n", " ") if el is not None else ""

        authors = [
            n.text.strip()
            for author in entry.findall("atom:author", ns)
            for n in [author.find("atom:name", ns)]
            if n is not None and n.text
        ]

        categories = []
        for cat in entry.findall("arxiv:primary_category", ns):
            if t := cat.get("term"):
                categories.append(t)
        for cat in entry.findall("atom:category", ns):
            if (t := cat.get("term")) and t not in categories:
                categories.append(t)

        pdf_url = None
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
                break
        if not pdf_url:
            pdf_url = f"https://arxiv.org/pdf/{paper_id}"

        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "paper_id": paper_id,
                        "title": text("atom:title"),
                        "authors": authors,
                        "content_warning": CONTENT_WARNING,
                        "abstract": text("atom:summary"),
                        "categories": categories,
                        "published": text("atom:published"),
                        "pdf_url": pdf_url,
                    },
                    indent=2,
                ),
            )
        ]

    except ArxivRateLimitError as e:
        # Rate limit from _rate_limited_get (issues #277, #278)
        return _rate_limited_response(
            str(e),
            retry_after_seconds=e.retry_after_seconds,
            status_code=e.status_code,
        )
    except RuntimeError as e:
        # Timeout from _rate_limited_get
        return [
            types.TextContent(
                type="text", text=json.dumps({"status": "error", "message": str(e)})
            )
        ]
    except httpx.HTTPStatusError as e:
        # HTTP errors other than not-found (issue #278).
        # Never leak upstream status lines / URLs (issue #166).
        status = e.response.status_code if e.response is not None else "unknown"
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
    except Exception as e:
        logger.error(f"get_abstract error: {e}")
        return [
            types.TextContent(
                type="text", text=json.dumps({"status": "error", "message": str(e)})
            )
        ]
