"""Get paper abstract/metadata without downloading the full paper."""

import json
import logging
import time
from typing import Any, Dict, List

import mcp.types as types
from mcp.types import ToolAnnotations

from .search import (
    _rate_limited_get,
    ARXIV_HEADERS,
    ARXIV_API_URL,
    _MIN_REQUEST_INTERVAL,
    _last_request_time,
)
import httpx
import xml.etree.ElementTree as ET

logger = logging.getLogger("arxiv-mcp-server")

abstract_tool = types.Tool(
    name="get_abstract",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Fetch the abstract and metadata of an arXiv paper by ID, WITHOUT downloading the full paper. "
        "Use this before download_paper to assess relevance and save tokens. "
        "Returns: title, authors, abstract, categories, published date, and PDF URL. "
        "Workflow tip: search_papers -> get_abstract (check relevance) -> download_paper (if needed) -> read_paper."
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
    },
)


async def handle_get_abstract(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Fetch paper metadata via arXiv API without downloading the full paper."""
    try:
        paper_id = arguments["paper_id"].strip()
        if not paper_id:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "error", "message": "paper_id is required"}
                    ),
                )
            ]

        url = f"{ARXIV_API_URL}?id_list={paper_id}&max_results=1"

        async with httpx.AsyncClient(timeout=20.0) as client:
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
                        "abstract": text("atom:summary"),
                        "categories": categories,
                        "published": text("atom:published"),
                        "pdf_url": pdf_url,
                    },
                    indent=2,
                ),
            )
        ]

    except RuntimeError as e:
        # Rate limit or timeout from _rate_limited_get
        return [
            types.TextContent(
                type="text", text=json.dumps({"status": "error", "message": str(e)})
            )
        ]
    except Exception as e:
        logger.error(f"get_abstract error: {e}")
        return [
            types.TextContent(
                type="text", text=json.dumps({"status": "error", "message": str(e)})
            )
        ]
