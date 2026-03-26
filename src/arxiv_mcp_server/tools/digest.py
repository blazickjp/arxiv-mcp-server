"""Research digest generator with structured JSON output."""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import mcp.types as types

from ..clients.s2_client import S2Client
from ..store.sqlite_store import SQLiteStore
from ..tools.search import _raw_arxiv_search
from ..utils.formatters import format_paper_markdown, truncate_abstract
from ..utils.rate_limiter import arxiv_limiter

logger = logging.getLogger("arxiv-mcp-server")

# Stopwords for theme extraction
_THEME_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "not",
    "no", "nor", "so", "if", "then", "than", "too", "very", "just",
    "about", "above", "after", "again", "all", "also", "any", "because",
    "before", "between", "both", "each", "few", "more", "most", "other",
    "over", "own", "same", "some", "such", "into", "only", "through",
    "under", "until", "up", "down", "out", "off", "once", "here", "there",
    "when", "where", "why", "how", "what", "which", "who", "whom", "while",
    "during", "using", "based", "via", "new", "two", "one", "three",
    "first", "second", "however", "well", "us", "we", "our", "they",
    "their", "them", "he", "she", "his", "her", "him", "it", "its",
    "this", "that", "these", "those", "paper", "approach", "method",
    "methods", "results", "show", "proposed", "propose", "work", "study",
    "use", "used", "presents", "present", "demonstrate", "provides",
    "provide", "introduce", "introduces",
}


def _extract_themes(
    papers: List[Dict[str, Any]], top_n: int = 15
) -> List[Dict[str, Any]]:
    """Extract common themes/keywords from paper titles and abstracts.

    Uses simple term frequency extraction over titles and abstracts.

    Args:
        papers: List of paper dicts.
        top_n: Number of top themes to return.

    Returns:
        List of dicts with keyword and count.
    """
    word_counts: Counter[str] = Counter()

    for paper in papers:
        text = (
            f"{paper.get('title', '')} {paper.get('abstract', '')}"
        ).lower()
        words = text.split()
        for word in words:
            cleaned = "".join(c for c in word if c.isalnum() or c == "-")
            if (
                cleaned
                and cleaned not in _THEME_STOPWORDS
                and len(cleaned) > 2
            ):
                word_counts[cleaned] += 1

    return [
        {"keyword": kw, "count": count}
        for kw, count in word_counts.most_common(top_n)
    ]


def _format_digest_markdown(digest: Dict[str, Any]) -> str:
    """Format a digest as human-readable markdown.

    Args:
        digest: Full digest dict.

    Returns:
        Markdown formatted string.
    """
    meta = digest.get("digest_metadata", {})
    lines = [
        f"# Research Digest: {meta.get('topic', 'Unknown')}",
        f"*Generated: {meta.get('generated_at', 'N/A')}*",
        f"*Time range: {meta.get('time_range_days', '?')} days | "
        f"Total papers: {meta.get('total_papers', 0)}*",
        "",
    ]

    # Highlights
    highlights = digest.get("highlights", [])
    if highlights:
        lines.append("## Highlights")
        lines.append("")
        for i, paper in enumerate(highlights, 1):
            citation_str = ""
            if paper.get("citation_count") is not None:
                citation_str = f" [{paper['citation_count']} citations]"
            lines.append(
                f"{i}. **{paper.get('title', 'Untitled')}**{citation_str}"
            )
            authors = paper.get("authors", [])
            if authors:
                authors_str = ", ".join(authors[:3])
                if len(authors) > 3:
                    authors_str += f" et al. ({len(authors)} authors)"
                lines.append(f"   *{authors_str}*")
            lines.append(
                f"   {truncate_abstract(paper.get('abstract', ''), max_chars=200)}"
            )
            lines.append("")

    # Themes
    themes = digest.get("themes", [])
    if themes:
        lines.append("## Key Themes")
        theme_strs = [
            f"`{t['keyword']}` ({t['count']})" for t in themes[:10]
        ]
        lines.append(", ".join(theme_strs))
        lines.append("")

    # Stats
    stats = digest.get("stats", {})
    if stats:
        lines.append("## Statistics")
        top_cats = stats.get("top_categories", [])
        if top_cats:
            lines.append("**Top categories**: " + ", ".join(
                f"{c['category']} ({c['count']})" for c in top_cats[:5]
            ))
        top_auths = stats.get("top_authors", [])
        if top_auths:
            lines.append("**Top authors**: " + ", ".join(
                f"{a['author']} ({a['count']})" for a in top_auths[:5]
            ))
        lines.append("")

    return "\n".join(lines)


digest_tool = types.Tool(
    name="arxiv_research_digest",
    description="""Generate a structured research digest for a topic.

Searches arXiv for recent papers on a topic, optionally enriches them with
citation counts from Semantic Scholar, and produces a comprehensive digest
including:
- Highlights (top papers by citations or recency)
- Full paper list grouped by category
- Key themes extracted from titles and abstracts
- Statistics (top categories, top authors)

The digest is saved to local storage for later reference. Returns both
human-readable markdown and structured JSON.""",
    inputSchema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Research topic for the digest.",
                "minLength": 3,
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional arXiv categories to filter (e.g., ['cs.AI', 'cs.LG']).",
            },
            "time_range_days": {
                "type": "integer",
                "description": "Number of days to look back (default: 7, max: 90).",
                "default": 7,
                "minimum": 1,
                "maximum": 90,
            },
            "max_papers": {
                "type": "integer",
                "description": "Maximum papers to include in digest (default: 20, max: 50).",
                "default": 20,
                "minimum": 5,
                "maximum": 50,
            },
            "include_citation_counts": {
                "type": "boolean",
                "description": "Whether to fetch citation counts from Semantic Scholar (default: true).",
                "default": True,
            },
        },
        "required": ["topic"],
    },
)


async def handle_digest(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle research digest generation requests.

    Searches arXiv for papers matching the topic, optionally fetches citation
    counts from S2, groups by category, and produces a structured digest.

    Args:
        arguments: Tool input with topic, optional categories, time_range_days,
            max_papers, and include_citation_counts.

    Returns:
        List containing a single TextContent with markdown + JSON digest.
    """
    try:
        topic = arguments["topic"]
        categories = arguments.get("categories")
        time_range_days = min(
            max(int(arguments.get("time_range_days", 7)), 1), 90
        )
        max_papers = min(
            max(int(arguments.get("max_papers", 20)), 5), 50
        )
        include_citations = arguments.get("include_citation_counts", True)

        logger.info(
            f"Generating digest: topic='{topic}', days={time_range_days}, "
            f"max_papers={max_papers}"
        )

        # Calculate date range
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=time_range_days)).strftime(
            "%Y-%m-%d"
        )

        # Search arXiv
        await arxiv_limiter.wait()
        try:
            papers = await _raw_arxiv_search(
                query=topic,
                max_results=max_papers,
                sort_by="date",
                date_from=date_from,
                categories=categories,
            )
        except Exception as e:
            logger.error(f"arXiv search failed: {e}")
            return [
                types.TextContent(
                    type="text",
                    text=f"Error: arXiv search failed - {str(e)}",
                )
            ]

        if not papers:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "digest_metadata": {
                                "topic": topic,
                                "generated_at": now.isoformat(),
                                "time_range_days": time_range_days,
                                "total_papers": 0,
                            },
                            "note": "No papers found for this topic in the specified time range.",
                        },
                        indent=2,
                    ),
                )
            ]

        # Optionally fetch citation counts from S2
        s2_note: Optional[str] = None
        if include_citations:
            paper_ids = [
                p.get("id", "") for p in papers if p.get("id")
            ]
            if paper_ids:
                try:
                    s2_client = S2Client()
                    s2_results = await s2_client.batch_get_papers(
                        paper_ids,
                        fields="paperId,externalIds,citationCount,influentialCitationCount",
                    )
                    # Build lookup
                    s2_lookup: Dict[str, Dict[str, Any]] = {}
                    for s2_paper in s2_results:
                        ext_ids = s2_paper.get("externalIds", {})
                        arxiv_id = (
                            ext_ids.get("ArXiv", "") if ext_ids else ""
                        )
                        if arxiv_id:
                            s2_lookup[arxiv_id] = s2_paper

                    # Enrich papers with citation data
                    for paper in papers:
                        pid = paper.get("id", "").split("v")[0]
                        if pid in s2_lookup:
                            paper["citation_count"] = s2_lookup[pid].get(
                                "citationCount", 0
                            )
                            paper["influential_citation_count"] = s2_lookup[
                                pid
                            ].get("influentialCitationCount", 0)

                except Exception as e:
                    logger.warning(
                        f"S2 citation lookup failed (non-fatal): {e}"
                    )
                    s2_note = (
                        "Citation counts unavailable - "
                        "Semantic Scholar lookup failed."
                    )

        # Group papers by primary category
        papers_by_category: Dict[str, List[Dict[str, Any]]] = {}
        for paper in papers:
            cats = paper.get("categories", [])
            primary_cat = cats[0] if cats else "uncategorized"
            if primary_cat not in papers_by_category:
                papers_by_category[primary_cat] = []
            papers_by_category[primary_cat].append(paper)

        # Generate highlights: top 5 papers by citation count or recency
        sorted_for_highlights = sorted(
            papers,
            key=lambda p: (p.get("citation_count", 0) or 0, p.get("published", "")),
            reverse=True,
        )
        highlights = []
        for paper in sorted_for_highlights[:5]:
            highlights.append(
                {
                    "paper_id": paper.get("id", ""),
                    "title": paper.get("title", ""),
                    "authors": paper.get("authors", []),
                    "abstract": truncate_abstract(
                        paper.get("abstract", ""), max_chars=300
                    ),
                    "categories": paper.get("categories", []),
                    "published": paper.get("published", ""),
                    "citation_count": paper.get("citation_count"),
                    "url": paper.get("url", ""),
                    "arxiv_link": f"https://arxiv.org/abs/{paper.get('id', '')}",
                }
            )

        # Full paper list
        paper_list = []
        for paper in papers:
            paper_list.append(
                {
                    "paper_id": paper.get("id", ""),
                    "title": paper.get("title", ""),
                    "authors": paper.get("authors", []),
                    "abstract": truncate_abstract(
                        paper.get("abstract", ""), max_chars=300
                    ),
                    "categories": paper.get("categories", []),
                    "published": paper.get("published", ""),
                    "citation_count": paper.get("citation_count"),
                    "arxiv_link": f"https://arxiv.org/abs/{paper.get('id', '')}",
                }
            )

        # Extract themes
        themes = _extract_themes(papers, top_n=15)

        # Compute stats
        category_counts: Counter[str] = Counter()
        author_counts: Counter[str] = Counter()
        for paper in papers:
            for cat in paper.get("categories", []):
                category_counts[cat] += 1
            for author in paper.get("authors", []):
                author_counts[author] += 1

        top_categories = [
            {"category": cat, "count": count}
            for cat, count in category_counts.most_common(10)
        ]
        top_authors = [
            {"author": author, "count": count}
            for author, count in author_counts.most_common(10)
        ]

        # Build digest structure
        digest: Dict[str, Any] = {
            "digest_metadata": {
                "topic": topic,
                "generated_at": now.isoformat(),
                "time_range_days": time_range_days,
                "total_papers": len(papers),
            },
            "highlights": highlights,
            "papers": paper_list,
            "papers_by_category": {
                cat: [
                    {
                        "paper_id": p.get("id", ""),
                        "title": p.get("title", ""),
                        "published": p.get("published", ""),
                    }
                    for p in cat_papers
                ]
                for cat, cat_papers in papers_by_category.items()
            },
            "themes": themes,
            "stats": {
                "total_papers": len(papers),
                "top_categories": top_categories,
                "top_authors": top_authors,
            },
        }

        if s2_note:
            digest["note"] = s2_note

        # Save digest to SQLite
        try:
            store = SQLiteStore()
            digest_json = json.dumps(digest, indent=2)
            digest_id = await store.save_digest(
                topic=topic,
                paper_count=len(papers),
                digest_json=digest_json,
            )
            logger.info(f"Digest saved with ID {digest_id}")
        except Exception as e:
            logger.warning(f"Failed to save digest to SQLite (non-fatal): {e}")

        # Build response: markdown summary + JSON structure
        markdown = _format_digest_markdown(digest)
        digest_json_str = json.dumps(digest, indent=2)

        response_text = (
            f"{markdown}\n\n"
            f"---\n\n"
            f"## Raw JSON\n\n"
            f"```json\n{digest_json_str}\n```"
        )

        logger.info(
            f"Digest generated: {len(papers)} papers, "
            f"{len(highlights)} highlights, {len(themes)} themes"
        )

        return [
            types.TextContent(type="text", text=response_text)
        ]

    except Exception as e:
        logger.error(f"Unexpected digest error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
