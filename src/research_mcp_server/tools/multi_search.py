"""Multi-source academic search across arXiv, OpenAlex, and Crossref."""

import asyncio
import json
import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

import mcp.types as types

from ..clients.openalex_client import OpenAlexClient
from ..clients.crossref_client import CrossrefClient
from .search import _raw_arxiv_search

logger = logging.getLogger("research-mcp-server")

VALID_SOURCES = {"arxiv", "openalex", "crossref"}


def _normalize_arxiv_result(paper: dict[str, Any]) -> dict[str, Any]:
    """Convert an arXiv search result to the standard paper format.

    Args:
        paper: Raw arXiv result dict from ``_raw_arxiv_search``.

    Returns:
        Normalized paper dict consistent with OpenAlex/Crossref format.
    """
    return {
        "id": paper.get("id", ""),
        "source": "arxiv",
        "source_id": paper.get("id"),
        "doi": None,
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "abstract": paper.get("abstract", ""),
        "published_date": paper.get("published", "")[:10] if paper.get("published") else None,
        "citation_count": None,
        "categories": paper.get("categories", []),
        "url": paper.get("url"),
        "open_access": True,  # arXiv is always open access
        "venue": "arXiv",
    }


def _title_similarity(a: str, b: str) -> float:
    """Compute normalized title similarity between two papers.

    Args:
        a: First title string.
        b: Second title string.

    Returns:
        Float between 0.0 and 1.0 indicating similarity.
    """
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _deduplicate(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate papers by DOI or title similarity.

    When duplicates are found, the version with more metadata (citation count,
    abstract, etc.) is preferred.

    Args:
        papers: List of normalized paper dicts from multiple sources.

    Returns:
        Deduplicated list of paper dicts.
    """
    seen_dois: dict[str, int] = {}
    unique: list[dict[str, Any]] = []

    for paper in papers:
        doi = paper.get("doi")

        # Check DOI-based dedup
        if doi:
            clean_doi = doi.replace("https://doi.org/", "").lower()
            if clean_doi in seen_dois:
                # Keep the version with more information
                existing_idx = seen_dois[clean_doi]
                existing = unique[existing_idx]
                if _paper_richness(paper) > _paper_richness(existing):
                    unique[existing_idx] = paper
                continue
            seen_dois[clean_doi] = len(unique)
            unique.append(paper)
            continue

        # Check title-based dedup for papers without DOI
        is_dup = False
        title = paper.get("title", "")
        for idx, existing in enumerate(unique):
            if _title_similarity(title, existing.get("title", "")) > 0.85:
                if _paper_richness(paper) > _paper_richness(existing):
                    unique[idx] = paper
                is_dup = True
                break

        if not is_dup:
            unique.append(paper)

    return unique


def _paper_richness(paper: dict[str, Any]) -> int:
    """Score how much metadata a paper dict contains.

    Used to prefer richer records during deduplication.

    Args:
        paper: Normalized paper dict.

    Returns:
        Integer score — higher means more metadata.
    """
    score = 0
    if paper.get("abstract"):
        score += 3
    if paper.get("citation_count") is not None:
        score += 2
    if paper.get("doi"):
        score += 1
    if paper.get("authors"):
        score += 1
    if paper.get("categories"):
        score += 1
    if paper.get("venue"):
        score += 1
    return score


def _sort_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort papers by citation count (descending) then recency (descending).

    Papers with citation counts come first (sorted highest to lowest),
    followed by papers without counts. Within each tier, newer papers
    appear first.

    Args:
        papers: List of normalized paper dicts.

    Returns:
        Sorted list of paper dicts.
    """
    return sorted(
        papers,
        key=lambda p: (
            # Tier: 0 = has citations (first), 1 = no citations (last)
            0 if p.get("citation_count") is not None else 1,
            # Within tier: highest citation count first
            -(p.get("citation_count") or 0),
            # Within same count: newest date first (reverse lexicographic)
            _invert_date(p.get("published_date")),
        ),
    )


def _invert_date(date_str: Optional[str]) -> str:
    """Invert a date string for descending sort.

    Args:
        date_str: ISO date string (YYYY-MM-DD) or None.

    Returns:
        Inverted string so that lexicographic sort gives descending date order.
    """
    if not date_str:
        return "9999-99-99"
    # Invert each digit: '2024' -> '7975'
    return "".join(
        str(9 - int(c)) if c.isdigit() else c for c in date_str
    )


async def _search_arxiv(
    query: str,
    max_results: int,
    date_from: Optional[str],
) -> list[dict[str, Any]]:
    """Search arXiv and normalize results.

    Args:
        query: Search query string.
        max_results: Maximum results to return.
        date_from: Optional start date (YYYY-MM-DD).

    Returns:
        List of normalized paper dicts.
    """
    try:
        raw_results = await _raw_arxiv_search(
            query=query,
            max_results=max_results,
            date_from=date_from,
        )
        return [_normalize_arxiv_result(r) for r in raw_results]
    except Exception as e:
        logger.warning(f"arXiv search failed: {e}")
        return []


async def _search_openalex(
    query: str,
    max_results: int,
    date_from: Optional[str],
) -> list[dict[str, Any]]:
    """Search OpenAlex and return normalized results.

    Args:
        query: Search query string.
        max_results: Maximum results to return.
        date_from: Optional start date (YYYY-MM-DD).

    Returns:
        List of normalized paper dicts.
    """
    try:
        client = OpenAlexClient()
        filters: Optional[dict[str, str]] = None
        if date_from:
            filters = {"from_publication_date": date_from}
        return await client.search_works(
            query=query,
            per_page=max_results,
            filters=filters,
        )
    except Exception as e:
        logger.warning(f"OpenAlex search failed: {e}")
        return []


async def _search_crossref(
    query: str,
    max_results: int,
) -> list[dict[str, Any]]:
    """Search Crossref and return normalized results.

    Args:
        query: Search query string.
        max_results: Maximum results to return.

    Returns:
        List of normalized paper dicts.
    """
    try:
        client = CrossrefClient()
        return await client.search_works(query=query, rows=max_results)
    except Exception as e:
        logger.warning(f"Crossref search failed: {e}")
        return []


multi_search_tool = types.Tool(
    name="cross_search",
    description="""Search across multiple academic sources (arXiv, OpenAlex, Crossref) in parallel. Returns deduplicated, merged results sorted by citation count and recency.

Use this when you want broader coverage than a single source. OpenAlex provides citation counts and open access info. Crossref provides DOI metadata and reference lists. arXiv provides full-text preprints.

Results are deduplicated by DOI and title similarity, with the richest metadata version kept.

Examples: query="transformer architecture", sources=["arxiv", "openalex"] | query="CRISPR gene editing", sources=["openalex", "crossref"], date_from="2023-01-01" | query="large language models", max_results_per_source=15""",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — free-text keywords or phrases.",
            },
            "sources": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["arxiv", "openalex", "crossref"],
                },
                "description": (
                    "Which sources to search. Default: ['arxiv', 'openalex']. "
                    "Add 'crossref' for DOI-heavy searches."
                ),
            },
            "max_results_per_source": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "description": "Maximum results from each source. Default: 10.",
            },
            "date_from": {
                "type": "string",
                "description": (
                    "Only return papers published on or after this date (YYYY-MM-DD). "
                    "Applied to arXiv and OpenAlex; Crossref does not support date filtering here."
                ),
            },
            "include_citations": {
                "type": "boolean",
                "description": (
                    "Include citation counts from OpenAlex. Default: true. "
                    "If OpenAlex is not in sources, this adds a lightweight citation "
                    "enrichment pass for arXiv/Crossref results."
                ),
            },
        },
        "required": ["query"],
    },
)


async def handle_multi_search(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle multi-source academic search.

    Searches requested sources in parallel, deduplicates by DOI/title,
    and returns a unified result set sorted by citations then recency.

    Args:
        arguments: Tool input arguments.

    Returns:
        List containing a single TextContent with JSON results.
    """
    try:
        query = arguments["query"]
        sources = arguments.get("sources", ["arxiv", "openalex"])
        max_per_source = min(int(arguments.get("max_results_per_source", 10)), 25)
        date_from: Optional[str] = arguments.get("date_from")
        include_citations = arguments.get("include_citations", True)

        # Validate sources
        invalid = set(sources) - VALID_SOURCES
        if invalid:
            return [
                types.TextContent(
                    type="text",
                    text=f"Error: Invalid sources: {', '.join(invalid)}. "
                    f"Valid options: {', '.join(sorted(VALID_SOURCES))}",
                )
            ]

        # Ensure OpenAlex is searched if citation enrichment is requested
        # and it's not already in the source list
        search_openalex_for_citations = (
            include_citations and "openalex" not in sources
        )

        # Build parallel search tasks
        tasks: list[tuple[str, Any]] = []
        for src in sources:
            if src == "arxiv":
                tasks.append(("arxiv", _search_arxiv(query, max_per_source, date_from)))
            elif src == "openalex":
                tasks.append(("openalex", _search_openalex(query, max_per_source, date_from)))
            elif src == "crossref":
                tasks.append(("crossref", _search_crossref(query, max_per_source)))

        # If we need citation data but OpenAlex isn't a primary source,
        # run a small OpenAlex query on the side
        if search_openalex_for_citations:
            tasks.append(("openalex_citations", _search_openalex(query, max_per_source, date_from)))

        # Execute all searches in parallel
        coros = [t[1] for t in tasks]
        task_names = [t[0] for t in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)

        # Collect results per source
        all_papers: list[dict[str, Any]] = []
        source_counts: dict[str, int] = {}
        errors: list[str] = []

        for name, result in zip(task_names, results):
            if isinstance(result, Exception):
                errors.append(f"{name}: {result}")
                logger.warning(f"Search failed for {name}: {result}")
                continue
            if isinstance(result, list):
                # Don't include openalex_citations results directly —
                # they are only for enrichment
                if name == "openalex_citations":
                    # Use these to enrich citation counts on other results
                    _enrich_citations(all_papers, result)
                else:
                    source_counts[name] = len(result)
                    all_papers.extend(result)

        if not all_papers:
            error_detail = f" Errors: {'; '.join(errors)}" if errors else ""
            return [
                types.TextContent(
                    type="text",
                    text=f"No results found across {', '.join(sources)}.{error_detail}",
                )
            ]

        # Deduplicate and sort
        deduplicated = _deduplicate(all_papers)
        sorted_papers = _sort_papers(deduplicated)

        response = {
            "query": query,
            "sources_searched": list(source_counts.keys()),
            "results_per_source": source_counts,
            "total_before_dedup": sum(source_counts.values()),
            "total_results": len(sorted_papers),
            "papers": sorted_papers,
        }

        if errors:
            response["warnings"] = errors

        return [
            types.TextContent(
                type="text",
                text=json.dumps(response, indent=2, default=str),
            )
        ]

    except Exception as e:
        logger.error(f"Multi-search error: {e}")
        return [
            types.TextContent(
                type="text",
                text=f"Error during multi-source search: {e}",
            )
        ]


def _enrich_citations(
    papers: list[dict[str, Any]],
    openalex_results: list[dict[str, Any]],
) -> None:
    """Enrich papers with citation counts from OpenAlex results.

    Matches by DOI or title similarity and fills in missing citation counts.
    Mutates ``papers`` in place.

    Args:
        papers: Papers to enrich (mutated in place).
        openalex_results: OpenAlex results to use as citation source.
    """
    # Build a DOI lookup from OpenAlex results
    doi_map: dict[str, dict[str, Any]] = {}
    for oa in openalex_results:
        doi = oa.get("doi")
        if doi:
            clean = doi.replace("https://doi.org/", "").lower()
            doi_map[clean] = oa

    for paper in papers:
        if paper.get("citation_count") is not None:
            continue

        # Try DOI match
        doi = paper.get("doi")
        if doi:
            clean = doi.replace("https://doi.org/", "").lower()
            match = doi_map.get(clean)
            if match and match.get("citation_count") is not None:
                paper["citation_count"] = match["citation_count"]
                continue

        # Try title match
        title = paper.get("title", "")
        for oa in openalex_results:
            if _title_similarity(title, oa.get("title", "")) > 0.85:
                if oa.get("citation_count") is not None:
                    paper["citation_count"] = oa["citation_count"]
                break
