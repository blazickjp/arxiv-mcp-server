"""Export paper metadata in various formats (BibTeX, Markdown, JSON, CSV).

Fetches paper metadata by arXiv ID and formats the output using helpers
from ``..utils.formatters``. Optionally enriches with citation counts from
the Semantic Scholar API.
"""

import arxiv
import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.s2_client import S2Client
from ..utils.formatters import (
    format_bibtex_entry,
    format_paper_json,
    format_paper_markdown,
)

logger = logging.getLogger("arxiv-mcp-server")

export_tool = types.Tool(
    name="arxiv_export",
    description=(
        "Export arXiv paper metadata in BibTeX, Markdown, JSON, or CSV format. "
        "Accepts a list of paper IDs and returns formatted output. "
        "Optionally includes citation counts from Semantic Scholar."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 50,
                "description": (
                    "List of arXiv paper IDs to export "
                    '(e.g. ["2401.12345", "2305.67890"]). '
                    "Between 1 and 50 IDs."
                ),
            },
            "format": {
                "type": "string",
                "default": "bibtex",
                "enum": ["bibtex", "markdown", "json", "csv"],
                "description": "Output format (default: bibtex).",
            },
            "include_abstract": {
                "type": "boolean",
                "default": True,
                "description": "Include abstracts in the output (default: true).",
            },
            "include_citation_count": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Fetch citation counts from Semantic Scholar "
                    "(default: false). Adds latency."
                ),
            },
        },
        "required": ["paper_ids"],
    },
    annotations=types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)


def _process_paper(paper: arxiv.Result) -> Dict[str, Any]:
    """Convert an ``arxiv.Result`` to a plain dict.

    Args:
        paper: Result object from the ``arxiv`` package.

    Returns:
        Paper metadata dict with consistent keys.
    """
    return {
        "id": paper.get_short_id(),
        "title": paper.title,
        "authors": [author.name for author in paper.authors],
        "abstract": paper.summary,
        "categories": paper.categories,
        "published": paper.published.isoformat(),
        "url": paper.pdf_url,
    }


async def _fetch_papers_by_ids(
    paper_ids: List[str],
) -> List[Dict[str, Any]]:
    """Fetch paper metadata from arXiv by ID list.

    Args:
        paper_ids: List of arXiv paper IDs.

    Returns:
        List of paper dicts for papers that were found.
    """
    client = arxiv.Client()
    search = arxiv.Search(id_list=paper_ids)

    papers: List[Dict[str, Any]] = []
    for result in client.results(search):
        papers.append(_process_paper(result))

    return papers


async def _enrich_with_citations(
    papers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Add citation counts to papers via Semantic Scholar batch API.

    Args:
        papers: List of paper dicts (must contain ``id`` key).

    Returns:
        Same list with ``citation_count`` added to each paper where available.
    """
    arxiv_ids = [p["id"] for p in papers]
    try:
        s2_client = S2Client()
        s2_results = await s2_client.batch_get_papers(
            arxiv_ids, fields="paperId,citationCount"
        )

        # Build lookup by arXiv ID
        citation_map: Dict[str, int] = {}
        for s2_paper in s2_results:
            ext_ids = s2_paper.get("externalIds", {})
            arxiv_id = ext_ids.get("ArXiv", "") if ext_ids else ""
            if arxiv_id and s2_paper.get("citationCount") is not None:
                citation_map[arxiv_id] = s2_paper["citationCount"]

        for paper in papers:
            clean_id = paper["id"].split("v")[0] if "v" in paper["id"] else paper["id"]
            paper["citation_count"] = citation_map.get(clean_id)

    except Exception as exc:
        logger.warning(
            "Failed to fetch citation counts from Semantic Scholar: %s", exc
        )
        for paper in papers:
            paper["citation_count"] = None

    return papers


def _format_csv(
    papers: List[Dict[str, Any]],
    include_abstract: bool,
) -> str:
    """Format papers as CSV text.

    Args:
        papers: List of paper dicts.
        include_abstract: Whether to include the abstract column.

    Returns:
        CSV-formatted string with headers.
    """
    columns = ["id", "title", "authors", "published", "categories", "url"]
    if include_abstract:
        columns.append("abstract")
    if any(p.get("citation_count") is not None for p in papers):
        columns.append("citation_count")

    def _escape(value: str) -> str:
        """Escape a CSV field value."""
        if "," in value or '"' in value or "\n" in value:
            return '"' + value.replace('"', '""') + '"'
        return value

    lines = [",".join(columns)]
    for paper in papers:
        row: List[str] = []
        for col in columns:
            val = paper.get(col, "")
            if isinstance(val, list):
                val = "; ".join(str(v) for v in val)
            elif val is None:
                val = ""
            else:
                val = str(val)
            row.append(_escape(val))
        lines.append(",".join(row))

    return "\n".join(lines)


def _format_output(
    papers: List[Dict[str, Any]],
    fmt: str,
    include_abstract: bool,
) -> str:
    """Format papers in the requested output format.

    Args:
        papers: List of paper dicts.
        fmt: One of ``bibtex``, ``markdown``, ``json``, ``csv``.
        include_abstract: Whether to include abstracts.

    Returns:
        Formatted string ready for the tool response.
    """
    if fmt == "bibtex":
        entries = [format_bibtex_entry(p) for p in papers]
        return "\n\n".join(entries)

    if fmt == "markdown":
        if not include_abstract:
            stripped = []
            for p in papers:
                copy = dict(p)
                copy["abstract"] = ""
                stripped.append(copy)
            papers = stripped
        blocks = [format_paper_markdown(p) for p in papers]
        return "\n\n---\n\n".join(blocks)

    if fmt == "json":
        cleaned = []
        for p in papers:
            entry = format_paper_json(p)
            if not include_abstract:
                entry.pop("abstract", None)
            cleaned.append(entry)
        return json.dumps(cleaned, indent=2)

    if fmt == "csv":
        if not include_abstract:
            for p in papers:
                p["abstract"] = ""
        return _format_csv(papers, include_abstract)

    # Fallback (should not happen given schema enum)
    return json.dumps(papers, indent=2)


async def handle_export(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle an export request for arXiv paper metadata.

    Args:
        arguments: Tool input matching the ``arxiv_export`` schema.

    Returns:
        List with a single ``TextContent`` containing the formatted output.
    """
    try:
        paper_ids = arguments["paper_ids"]
        fmt = arguments.get("format", "bibtex")
        include_abstract = arguments.get("include_abstract", True)
        include_citation_count = arguments.get("include_citation_count", False)

        if not paper_ids:
            return [
                types.TextContent(
                    type="text",
                    text="Error: At least one paper ID must be provided.",
                )
            ]

        if len(paper_ids) > 50:
            return [
                types.TextContent(
                    type="text",
                    text="Error: Maximum 50 paper IDs allowed per request.",
                )
            ]

        logger.info(
            "Export request — %d papers, format=%s, citations=%s",
            len(paper_ids),
            fmt,
            include_citation_count,
        )

        # Fetch paper metadata from arXiv
        papers = await _fetch_papers_by_ids(paper_ids)

        if not papers:
            return [
                types.TextContent(
                    type="text",
                    text="Error: No papers found for the provided IDs.",
                )
            ]

        # Optionally enrich with citation counts
        if include_citation_count:
            papers = await _enrich_with_citations(papers)

        # Format output
        output = _format_output(papers, fmt, include_abstract)

        return [types.TextContent(type="text", text=output)]

    except Exception as exc:
        logger.error("Unexpected error in export: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {exc}")]
