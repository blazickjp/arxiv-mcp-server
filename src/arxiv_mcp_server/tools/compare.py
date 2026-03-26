"""Side-by-side paper comparison tool."""

import json
import logging
import re
import string
from typing import Dict, Any, List, Optional

import arxiv
import mcp.types as types

from ..clients.s2_client import S2Client

logger = logging.getLogger("arxiv-mcp-server")

# Common English stop words to exclude from keyword overlap
STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "shall", "it",
    "its", "this", "that", "these", "those", "we", "our", "they", "their",
    "which", "what", "who", "whom", "how", "when", "where", "not", "no",
    "nor", "so", "if", "then", "than", "too", "very", "just", "about",
    "up", "out", "into", "over", "after", "before", "between", "under",
    "such", "each", "every", "all", "any", "both", "few", "more", "most",
    "other", "some", "only", "same", "also", "using", "used", "based",
    "show", "shows", "shown", "new", "proposed", "paper", "approach",
    "method", "results", "however", "while", "through", "during",
})


compare_tool = types.Tool(
    name="arxiv_compare_papers",
    description="""Compare 2-5 arXiv papers side by side. Use when you need to contrast methodologies, scope, or impact across specific papers. Returns a markdown table + JSON with metadata, abstract snippets, citation counts (via Semantic Scholar), and keyword overlap analysis. You must already have paper IDs.

Examples: paper_ids=["1706.03762", "2005.14165"] | paper_ids=["2401.12345", "2401.67890"], comparison_aspects=["methodology", "datasets"]""",
    inputSchema={
        "type": "object",
        "properties": {
            "paper_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 5,
                "description": "List of 2-5 arXiv paper IDs to compare (e.g., ['2401.12345', '1706.03762']).",
            },
            "comparison_aspects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Aspects to highlight in comparison. Defaults to ['methodology', 'results', 'datasets', 'novelty'].",
            },
        },
        "required": ["paper_ids"],
    },
)


def _extract_keywords(text: str, min_length: int = 3) -> set[str]:
    """Extract significant keywords from text.

    Args:
        text: Input text (typically an abstract).
        min_length: Minimum word length to consider.

    Returns:
        Set of lowercase significant words.
    """
    # Remove punctuation and lowercase
    cleaned = text.lower()
    cleaned = cleaned.translate(str.maketrans("", "", string.punctuation))
    words = cleaned.split()

    return {
        w for w in words
        if len(w) >= min_length and w not in STOP_WORDS and not w.isdigit()
    }


def _build_markdown_table(papers: List[Dict[str, Any]]) -> str:
    """Build a markdown comparison table from paper data.

    Args:
        papers: List of paper data dicts.

    Returns:
        Markdown-formatted table string.
    """
    if not papers:
        return "No papers to compare."

    lines: List[str] = []

    # Header row
    header = "| Aspect | " + " | ".join(
        f"Paper {i + 1}" for i in range(len(papers))
    ) + " |"
    separator = "|---|" + "|".join("---" for _ in papers) + "|"
    lines.append(header)
    lines.append(separator)

    # Title row
    row = "| **Title** | " + " | ".join(
        p.get("title", "N/A") for p in papers
    ) + " |"
    lines.append(row)

    # Authors row
    row = "| **Authors** | " + " | ".join(
        ", ".join(p.get("authors", [])[:3])
        + ("..." if len(p.get("authors", [])) > 3 else "")
        for p in papers
    ) + " |"
    lines.append(row)

    # Date row
    row = "| **Published** | " + " | ".join(
        p.get("published", "N/A") for p in papers
    ) + " |"
    lines.append(row)

    # Categories row
    row = "| **Categories** | " + " | ".join(
        ", ".join(p.get("categories", [])[:3]) for p in papers
    ) + " |"
    lines.append(row)

    # Citation count row
    row = "| **Citations** | " + " | ".join(
        str(p.get("citation_count", "N/A")) for p in papers
    ) + " |"
    lines.append(row)

    # Abstract snippet row
    row = "| **Abstract** | " + " | ".join(
        p.get("abstract_snippet", "N/A") for p in papers
    ) + " |"
    lines.append(row)

    return "\n".join(lines)


async def handle_compare(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle paper comparison requests.

    Fetches metadata from arXiv and optionally enriches with citation
    counts from Semantic Scholar. Performs keyword overlap analysis.
    """
    try:
        paper_ids: List[str] = arguments["paper_ids"]
        comparison_aspects: List[str] = arguments.get(
            "comparison_aspects",
            ["methodology", "results", "datasets", "novelty"],
        )

        # Validate count
        if len(paper_ids) < 2:
            return [
                types.TextContent(
                    type="text",
                    text="Error: At least 2 paper IDs are required for comparison.",
                )
            ]
        if len(paper_ids) > 5:
            return [
                types.TextContent(
                    type="text",
                    text="Error: Maximum 5 papers can be compared at once.",
                )
            ]

        # Fetch metadata from arXiv
        client = arxiv.Client()
        search = arxiv.Search(id_list=paper_ids)

        arxiv_papers: Dict[str, arxiv.Result] = {}
        for paper in client.results(search):
            short_id = paper.get_short_id()
            arxiv_papers[short_id] = paper

        if not arxiv_papers:
            return [
                types.TextContent(
                    type="text",
                    text="Error: No papers found for the given IDs. Check that the IDs are valid arXiv paper IDs.",
                )
            ]

        # Optionally enrich with S2 citation data (graceful degradation)
        s2_data: Dict[str, Dict[str, Any]] = {}
        try:
            s2_client = S2Client()
            s2_papers = await s2_client.batch_get_papers(paper_ids)
            for s2_paper in s2_papers:
                ext_ids = s2_paper.get("externalIds", {})
                arxiv_ext_id = ext_ids.get("ArXiv", "") if ext_ids else ""
                if arxiv_ext_id:
                    s2_data[arxiv_ext_id] = s2_paper
        except Exception as e:
            logger.warning(
                f"S2 enrichment failed (continuing without citation counts): {e}"
            )

        # Build comparison data
        papers_data: List[Dict[str, Any]] = []
        all_keywords: List[set[str]] = []

        for pid in paper_ids:
            # Match arXiv result by ID (strip version)
            clean_pid = pid.split("v")[0] if "v" in pid else pid
            paper = None
            for key, val in arxiv_papers.items():
                if clean_pid in key:
                    paper = val
                    break

            if paper is None:
                papers_data.append({
                    "id": pid,
                    "title": "Not found",
                    "authors": [],
                    "published": "N/A",
                    "categories": [],
                    "abstract_snippet": "N/A",
                    "citation_count": "N/A",
                })
                all_keywords.append(set())
                continue

            abstract = paper.summary or ""
            snippet = abstract[:200] + "..." if len(abstract) > 200 else abstract
            snippet = snippet.replace("\n", " ")

            # Get citation count from S2 if available
            citation_count: Any = "N/A"
            s2_paper = s2_data.get(clean_pid)
            if s2_paper:
                citation_count = s2_paper.get("citationCount", "N/A")

            keywords = _extract_keywords(abstract)
            all_keywords.append(keywords)

            papers_data.append({
                "id": paper.get_short_id(),
                "title": paper.title,
                "authors": [a.name for a in paper.authors],
                "published": paper.published.isoformat() if paper.published else "N/A",
                "categories": paper.categories,
                "abstract_snippet": snippet,
                "citation_count": citation_count,
            })

        # Keyword overlap analysis
        keyword_overlap: Dict[str, Any] = {}
        if len(all_keywords) >= 2:
            # Pairwise overlaps
            pairwise: List[Dict[str, Any]] = []
            for i in range(len(all_keywords)):
                for j in range(i + 1, len(all_keywords)):
                    common = all_keywords[i] & all_keywords[j]
                    if common:
                        pairwise.append({
                            "paper_a": papers_data[i].get("id", f"paper_{i}"),
                            "paper_b": papers_data[j].get("id", f"paper_{j}"),
                            "common_keywords": sorted(common),
                            "overlap_count": len(common),
                        })

            # Keywords common to all papers
            if all(kw for kw in all_keywords):
                universal = all_keywords[0]
                for kw_set in all_keywords[1:]:
                    universal = universal & kw_set
                keyword_overlap["common_to_all"] = sorted(universal)

            keyword_overlap["pairwise"] = pairwise

        # Build markdown table
        markdown_table = _build_markdown_table(papers_data)

        # Assemble response
        response = {
            "comparison": {
                "paper_count": len(papers_data),
                "aspects": comparison_aspects,
                "papers": papers_data,
                "keyword_overlap": keyword_overlap,
            },
            "markdown_table": markdown_table,
        }

        return [
            types.TextContent(type="text", text=json.dumps(response, indent=2))
        ]

    except arxiv.ArxivError as e:
        logger.error(f"arXiv API error during comparison: {e}")
        return [
            types.TextContent(
                type="text", text=f"Error: arXiv API error - {str(e)}"
            )
        ]
    except Exception as e:
        logger.error(f"Unexpected comparison error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
