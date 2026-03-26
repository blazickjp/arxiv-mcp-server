"""Publication trend analysis for a topic over time."""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import mcp.types as types

from ..clients.s2_client import S2Client
from ..tools.search import _raw_arxiv_search

logger = logging.getLogger("research-mcp-server")

# Common English stopwords for keyword extraction
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "dare", "ought", "used", "this", "that", "these", "those", "it", "its",
    "we", "our", "they", "their", "them", "he", "she", "his", "her", "him",
    "not", "no", "nor", "so", "if", "then", "than", "too", "very", "just",
    "about", "above", "after", "again", "all", "also", "am", "any", "because",
    "before", "between", "both", "each", "few", "more", "most", "other",
    "over", "own", "same", "some", "such", "into", "only", "through",
    "under", "until", "up", "down", "out", "off", "once", "here", "there",
    "when", "where", "why", "how", "what", "which", "who", "whom", "while",
    "during", "using", "based", "via", "new", "two", "one", "three",
    "first", "second", "however", "well", "also", "us", "paper", "approach",
    "method", "methods", "results", "show", "proposed", "propose", "model",
    "models", "data", "problem", "work", "study",
}


def _extract_keywords(
    titles: List[str], top_n: int = 20
) -> List[Dict[str, Any]]:
    """Extract top keywords from a list of titles.

    Args:
        titles: List of paper titles.
        top_n: Number of top keywords to return.

    Returns:
        List of dicts with keyword and count.
    """
    word_counts: Counter[str] = Counter()
    for title in titles:
        words = title.lower().split()
        for word in words:
            # Strip punctuation
            cleaned = "".join(c for c in word if c.isalnum() or c == "-")
            if cleaned and cleaned not in STOPWORDS and len(cleaned) > 2:
                word_counts[cleaned] += 1

    return [
        {"keyword": kw, "count": count}
        for kw, count in word_counts.most_common(top_n)
    ]


def _bucket_papers(
    papers: List[Dict[str, Any]], granularity: str
) -> Dict[str, List[Dict[str, Any]]]:
    """Bucket papers by publication date.

    Args:
        papers: List of paper dicts with 'published' field.
        granularity: 'weekly' or 'monthly'.

    Returns:
        Dict mapping period label to list of papers in that period.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for paper in papers:
        published = paper.get("published", "")
        if not published:
            continue

        try:
            # Parse ISO 8601 date
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(published[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue

        if granularity == "weekly":
            # ISO week: YYYY-WNN
            iso_year, iso_week, _ = dt.isocalendar()
            label = f"{iso_year}-W{iso_week:02d}"
        else:
            # Monthly: YYYY-MM
            label = dt.strftime("%Y-%m")

        buckets[label].append(paper)

    return dict(sorted(buckets.items()))


def _identify_emerging_terms(
    buckets: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Identify terms that appear more frequently in recent periods.

    Splits the time range into two halves (older vs recent) and finds terms
    with higher relative frequency in the recent half.

    Args:
        buckets: Papers bucketed by time period.

    Returns:
        List of emerging term dicts with term, recent_count, older_count.
    """
    sorted_periods = sorted(buckets.keys())
    if len(sorted_periods) < 2:
        return []

    midpoint = len(sorted_periods) // 2
    older_periods = sorted_periods[:midpoint]
    recent_periods = sorted_periods[midpoint:]

    older_titles = []
    for period in older_periods:
        for paper in buckets[period]:
            older_titles.append(paper.get("title", ""))

    recent_titles = []
    for period in recent_periods:
        for paper in buckets[period]:
            recent_titles.append(paper.get("title", ""))

    older_kw = {
        item["keyword"]: item["count"]
        for item in _extract_keywords(older_titles, top_n=50)
    }
    recent_kw = {
        item["keyword"]: item["count"]
        for item in _extract_keywords(recent_titles, top_n=50)
    }

    # Find terms with higher relative frequency in recent half
    older_total = max(len(older_titles), 1)
    recent_total = max(len(recent_titles), 1)

    emerging = []
    for term, recent_count in recent_kw.items():
        older_count = older_kw.get(term, 0)
        recent_rate = recent_count / recent_total
        older_rate = older_count / older_total

        # Term is emerging if it appears at least 50% more frequently
        # in recent period, or is entirely new
        if recent_rate > older_rate * 1.5 or (older_count == 0 and recent_count >= 2):
            emerging.append(
                {
                    "term": term,
                    "recent_count": recent_count,
                    "older_count": older_count,
                    "growth_ratio": round(
                        recent_rate / max(older_rate, 0.001), 2
                    ),
                }
            )

    emerging.sort(key=lambda x: x["growth_ratio"], reverse=True)
    return emerging[:15]


trend_analysis_tool = types.Tool(
    name="trends",
    description="""Analyze how a research topic is evolving over time on arXiv. Use when you want to understand publication volume trends, identify emerging terms, or find prolific authors -- not for finding specific papers (use search_papers for that).

Returns: publication volume (weekly/monthly), top keywords, emerging terms, top authors, and top papers with optional citation counts. Searches up to 200 papers over 1-36 months.

Examples: topic="large language models", categories=["cs.CL"] | topic="quantum error correction", time_range_months=24, granularity="monthly\"""",
    inputSchema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Research topic to analyze trends for.",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional arXiv categories to filter (e.g., ['cs.AI', 'cs.LG']).",
            },
            "time_range_months": {
                "type": "integer",
                "description": "Number of months to look back (default: 12, max: 36).",
                "default": 12,
                "minimum": 1,
                "maximum": 36,
            },
            "granularity": {
                "type": "string",
                "description": "Time bucket granularity: 'weekly' or 'monthly' (default: 'monthly').",
                "enum": ["weekly", "monthly"],
                "default": "monthly",
            },
        },
        "required": ["topic"],
    },
)


async def handle_trend_analysis(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle publication trend analysis requests.

    Searches arXiv for papers on a topic, buckets them by time period,
    extracts keywords, identifies emerging terms, and ranks authors.

    Args:
        arguments: Tool input with topic, optional categories,
            time_range_months, and granularity.

    Returns:
        List containing a single TextContent with JSON trend analysis.
    """
    try:
        topic = arguments["topic"]
        categories = arguments.get("categories")
        time_range_months = min(
            max(int(arguments.get("time_range_months", 12)), 1), 36
        )
        granularity = arguments.get("granularity", "monthly")
        if granularity not in ("weekly", "monthly"):
            granularity = "monthly"

        logger.info(
            f"Trend analysis: topic='{topic}', range={time_range_months}mo, "
            f"granularity={granularity}"
        )

        # Calculate date_from based on time range
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=time_range_months * 30)).strftime(
            "%Y-%m-%d"
        )

        # Search arXiv for papers in the time range
        # Use a large pool to get good trend data
        try:
            papers = await _raw_arxiv_search(
                query=topic,
                max_results=200,
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
                            "topic": topic,
                            "total_papers": 0,
                            "note": "No papers found for this topic in the specified time range.",
                        },
                        indent=2,
                    ),
                )
            ]

        # Bucket papers by time period
        buckets = _bucket_papers(papers, granularity)

        # Volume over time
        volume_over_time = {
            period: len(papers_in_period)
            for period, papers_in_period in buckets.items()
        }

        # Extract keywords from all titles
        all_titles = [p.get("title", "") for p in papers]
        top_keywords = _extract_keywords(all_titles, top_n=20)

        # Identify emerging terms
        emerging_terms = _identify_emerging_terms(buckets)

        # Top authors by paper count
        author_counts: Counter[str] = Counter()
        for paper in papers:
            for author in paper.get("authors", []):
                author_counts[author] += 1

        top_authors = [
            {"author": author, "paper_count": count}
            for author, count in author_counts.most_common(15)
        ]

        # Top papers (first 10 by recency, since they're sorted by date)
        top_papers = []
        for paper in papers[:10]:
            top_papers.append(
                {
                    "id": paper.get("id", ""),
                    "title": paper.get("title", ""),
                    "authors": paper.get("authors", [])[:5],
                    "published": paper.get("published", ""),
                    "categories": paper.get("categories", []),
                    "url": paper.get("url", ""),
                }
            )

        # Optionally try S2 batch lookup for citation counts on top papers
        s2_note: Optional[str] = None
        top_paper_ids = [p["id"] for p in top_papers if p.get("id")]
        if top_paper_ids:
            try:
                s2_client = S2Client()
                s2_results = await s2_client.batch_get_papers(
                    top_paper_ids,
                    fields="paperId,citationCount,influentialCitationCount",
                )
                # Build lookup by arXiv ID
                s2_lookup: Dict[str, Dict[str, Any]] = {}
                for s2_paper in s2_results:
                    ext_ids = s2_paper.get("externalIds", {})
                    arxiv_id = ext_ids.get("ArXiv", "") if ext_ids else ""
                    if arxiv_id:
                        s2_lookup[arxiv_id] = s2_paper

                for paper in top_papers:
                    pid = paper["id"].split("v")[0]
                    if pid in s2_lookup:
                        paper["citation_count"] = s2_lookup[pid].get(
                            "citationCount", 0
                        )
                        paper["influential_citation_count"] = s2_lookup[
                            pid
                        ].get("influentialCitationCount", 0)
            except Exception as e:
                logger.warning(f"S2 citation lookup failed (non-fatal): {e}")
                s2_note = (
                    "Citation counts unavailable - Semantic Scholar lookup failed."
                )

        response: Dict[str, Any] = {
            "topic": topic,
            "time_range_months": time_range_months,
            "granularity": granularity,
            "total_papers": len(papers),
            "volume_over_time": volume_over_time,
            "top_keywords": top_keywords,
            "emerging_terms": emerging_terms,
            "top_authors": top_authors,
            "top_papers": top_papers,
        }

        if s2_note:
            response["note"] = s2_note

        logger.info(
            f"Trend analysis completed: {len(papers)} papers across "
            f"{len(buckets)} periods"
        )

        return [
            types.TextContent(
                type="text", text=json.dumps(response, indent=2)
            )
        ]

    except Exception as e:
        logger.error(f"Unexpected trend analysis error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
