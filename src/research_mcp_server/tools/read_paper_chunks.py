"""Semantic section-based paper reading for the arXiv MCP server."""

import json
import re
import logging
from typing import Any, Dict, List, Optional

import arxiv
import mcp.types as types

from ..config import Settings
from ..resources.papers import PaperManager

logger = logging.getLogger("research-mcp-server")

VALID_SECTIONS = [
    "abstract",
    "introduction",
    "related_work",
    "methodology",
    "results",
    "discussion",
    "conclusion",
    "references",
]

SECTION_PATTERNS: Dict[str, List[str]] = {
    "abstract": ["abstract"],
    "introduction": ["introduction", "intro"],
    "related_work": ["related work", "background", "prior work", "literature"],
    "methodology": ["method", "approach", "model", "architecture", "framework", "system"],
    "results": ["result", "experiment", "evaluation", "performance"],
    "discussion": ["discussion", "analysis"],
    "conclusion": ["conclusion", "summary", "future work"],
    "references": ["reference", "bibliography"],
}

read_paper_chunks_tool = types.Tool(
    name="read_paper_chunks",
    description=(
        "Read a paper split into semantically coherent sections instead of raw text. "
        "Returns structured JSON with classified sections (abstract, introduction, "
        "methodology, results, etc.). Optionally filter to specific sections."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "The arXiv ID of the paper to read",
            },
            "sections": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific sections to return. Options: abstract, introduction, "
                    "related_work, methodology, results, discussion, conclusion, "
                    "references. If omitted, all sections are returned."
                ),
            },
            "include_metadata": {
                "type": "boolean",
                "description": "Include paper metadata (title, authors, categories). Default: true.",
                "default": True,
            },
        },
        "required": ["paper_id"],
    },
)


def _classify_heading(heading_text: str) -> str:
    """Classify a heading into a known section name.

    Args:
        heading_text: The raw heading text from the markdown.

    Returns:
        A section name from VALID_SECTIONS, or "other" if no match.
    """
    normalized = heading_text.lower().strip()
    # Strip leading numbering like "1.", "2.1", "III.", "A." etc.
    normalized = re.sub(r"^[\d]+[\.\)]\s*", "", normalized)
    normalized = re.sub(r"^[ivxlcdm]+[\.\)]\s*", "", normalized)
    normalized = re.sub(r"^[a-z][\.\)]\s*", "", normalized)
    normalized = normalized.strip()

    for section_name, patterns in SECTION_PATTERNS.items():
        for pattern in patterns:
            if pattern in normalized:
                return section_name

    return "other"


def _parse_sections(markdown: str) -> List[Dict[str, Any]]:
    """Parse markdown content into semantically classified sections.

    Splits on markdown headings (#, ##, ###) and classifies each section
    by matching heading text against known academic paper patterns.

    Args:
        markdown: The full markdown content of the paper.

    Returns:
        A list of section dicts with name, heading, and content.
    """
    heading_pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

    sections: List[Dict[str, Any]] = []
    matches = list(heading_pattern.finditer(markdown))

    # Content before the first heading is "preamble" (usually title + abstract)
    if matches:
        preamble = markdown[: matches[0].start()].strip()
        if preamble:
            # Check if the preamble contains an abstract-like block
            preamble_name = "preamble"
            preamble_lower = preamble.lower()
            if "abstract" in preamble_lower:
                preamble_name = "abstract"
            sections.append(
                {
                    "name": preamble_name,
                    "heading": "Preamble",
                    "content": preamble,
                    "char_count": len(preamble),
                }
            )
    else:
        # No headings found — entire document is one section
        content = markdown.strip()
        if content:
            sections.append(
                {
                    "name": "preamble",
                    "heading": "Full Document",
                    "content": content,
                    "char_count": len(content),
                }
            )
        return sections

    # Process each heading and its content
    for i, match in enumerate(matches):
        heading_text = match.group(2).strip()
        section_name = _classify_heading(heading_text)

        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        content = markdown[start:end].strip()

        sections.append(
            {
                "name": section_name,
                "heading": heading_text,
                "content": content,
                "char_count": len(content),
            }
        )

    return sections


async def _fetch_metadata(paper_id: str) -> Optional[Dict[str, Any]]:
    """Fetch paper metadata from arXiv.

    Args:
        paper_id: The arXiv paper ID.

    Returns:
        A dict with title, authors, and categories, or None on failure.
    """
    try:
        client = arxiv.Client()
        search = arxiv.Search(id_list=[paper_id])
        papers = list(client.results(search))
        if papers:
            paper = papers[0]
            return {
                "title": paper.title,
                "authors": [str(a) for a in paper.authors],
                "categories": paper.categories,
            }
    except Exception as e:
        logger.warning(f"Failed to fetch metadata for {paper_id}: {e}")

    return None


async def handle_read_paper_chunks(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle requests to read a paper as semantically classified sections.

    Args:
        arguments: Tool arguments containing paper_id and optional filters.

    Returns:
        A list containing a single TextContent with structured JSON.
    """
    try:
        paper_id: str = arguments["paper_id"]
        requested_sections: Optional[List[str]] = arguments.get("sections")
        include_metadata: bool = arguments.get("include_metadata", True)

        # Validate requested sections
        if requested_sections:
            invalid = [s for s in requested_sections if s not in VALID_SECTIONS]
            if invalid:
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "status": "error",
                                "message": (
                                    f"Invalid section names: {invalid}. "
                                    f"Valid options: {VALID_SECTIONS}"
                                ),
                            },
                            indent=2,
                        ),
                    )
                ]

        # Check if paper exists locally
        paper_manager = PaperManager()
        if not await paper_manager.has_paper(paper_id):
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": (
                                f"Paper {paper_id} not found in local storage. "
                                "Please download it first using the download_paper tool."
                            ),
                        },
                        indent=2,
                    ),
                )
            ]

        # Get markdown content
        content = await paper_manager.get_paper_content(paper_id)

        # Parse into sections
        all_sections = _parse_sections(content)

        # Filter sections if requested
        if requested_sections:
            filtered = [s for s in all_sections if s["name"] in requested_sections]
            sections = filtered
        else:
            sections = all_sections

        # Build result
        result: Dict[str, Any] = {
            "status": "success",
            "paper_id": paper_id,
            "total_sections": len(all_sections),
            "returned_sections": len(sections),
            "sections": sections,
        }

        # Fetch metadata if requested
        if include_metadata:
            metadata = await _fetch_metadata(paper_id)
            if metadata:
                result["metadata"] = metadata

        return [
            types.TextContent(
                type="text",
                text=json.dumps(result, indent=2),
            )
        ]

    except Exception as e:
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "message": f"Error reading paper sections: {str(e)}",
                    },
                    indent=2,
                ),
            )
        ]
