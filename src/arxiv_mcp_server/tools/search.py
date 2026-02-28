"""Search functionality for the arXiv MCP server."""

import arxiv
import json
import logging
import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from dateutil import parser
import mcp.types as types
from ..config import Settings, get_arxiv_client

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

# arXiv API endpoint for raw queries (bypasses arxiv package URL encoding issues)
# Use HTTPS to avoid redirect from http -> https
ARXIV_API_URL = "https://export.arxiv.org/api/query"

# XML namespaces used in arXiv Atom feed
ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# Valid arXiv category prefixes for validation
VALID_CATEGORIES = {
    "cs",
    "econ",
    "eess",
    "math",
    "physics",
    "q-bio",
    "q-fin",
    "stat",
    "astro-ph",
    "cond-mat",
    "gr-qc",
    "hep-ex",
    "hep-lat",
    "hep-ph",
    "hep-th",
    "math-ph",
    "nlin",
    "nucl-ex",
    "nucl-th",
    "quant-ph",
}


async def _raw_arxiv_search(
    query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Perform arXiv search using raw HTTP requests.

    This bypasses the arxiv Python package to avoid URL encoding issues
    with date filters. The arxiv package encodes '+' as '%2B' which breaks
    the submittedDate:[YYYYMMDD+TO+YYYYMMDD] syntax.
    """
    # Build query components
    query_parts = []

    if query.strip():
        query_parts.append(f"({query})")

    # Add category filtering
    if categories:
        category_filter = " OR ".join(f"cat:{cat}" for cat in categories)
        query_parts.append(f"({category_filter})")

    # Add date filtering using arXiv API syntax
    if date_from or date_to:
        try:
            if date_from:
                start_date = parser.parse(date_from).strftime("%Y%m%d0000")
            else:
                start_date = "199107010000"  # arXiv started July 1991

            if date_to:
                end_date = parser.parse(date_to).strftime("%Y%m%d2359")
            else:
                end_date = datetime.now().strftime("%Y%m%d2359")

            # CRITICAL: This must NOT be URL-encoded. The '+' in '+TO+' must remain literal.
            date_filter = f"submittedDate:[{start_date}+TO+{end_date}]"
            query_parts.append(date_filter)
            logger.debug(f"Added date filter: {date_filter}")
        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing dates: {e}")
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD format: {e}")

    if not query_parts:
        raise ValueError("No search criteria provided")

    # Combine query parts with AND (space in arXiv = AND)
    final_query = " AND ".join(query_parts)
    logger.debug(f"Raw API query: {final_query}")

    # Map sort parameter to arXiv API values
    sort_map = {
        "relevance": "relevance",
        "date": "submittedDate",
    }
    sort_order = "descending"

    # Build the URL manually to avoid encoding the '+' in date ranges
    # We encode most parameters but carefully preserve '+TO+' in date filters
    base_params = f"max_results={max_results}&sortBy={sort_map.get(sort_by, 'relevance')}&sortOrder={sort_order}"

    # Manually construct search_query parameter
    # We need to encode spaces and special chars BUT NOT the '+' in '+TO+'
    # Strategy: encode the query parts separately, then join with encoded AND
    encoded_query = (
        final_query.replace(" AND ", "+AND+").replace(" OR ", "+OR+").replace(" ", "+")
    )
    # But we need to be careful about existing '+TO+' - it should stay as-is
    # Since we built the date filter with literal '+TO+', it's already correct

    url = f"{ARXIV_API_URL}?search_query={encoded_query}&{base_params}"
    logger.debug(f"Raw API URL: {url}")

    # Make the request
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()

    # Parse the Atom XML response
    return _parse_arxiv_atom_response(response.text)


def _parse_arxiv_atom_response(xml_text: str) -> List[Dict[str, Any]]:
    """Parse arXiv Atom XML response into paper dictionaries."""
    results = []

    try:
        root = ET.fromstring(xml_text)

        for entry in root.findall("atom:entry", ARXIV_NS):
            # Extract paper ID from the id URL
            id_elem = entry.find("atom:id", ARXIV_NS)
            if id_elem is None or id_elem.text is None:
                continue

            # ID format: http://arxiv.org/abs/XXXX.XXXXX or http://arxiv.org/abs/category/XXXXXXX
            paper_id = id_elem.text.split("/abs/")[-1]
            # Remove version suffix for short ID
            short_id = paper_id.split("v")[0] if "v" in paper_id else paper_id

            # Title
            title_elem = entry.find("atom:title", ARXIV_NS)
            title = (
                title_elem.text.strip().replace("\n", " ")
                if title_elem is not None and title_elem.text
                else ""
            )

            # Authors
            authors = []
            for author in entry.findall("atom:author", ARXIV_NS):
                name_elem = author.find("atom:name", ARXIV_NS)
                if name_elem is not None and name_elem.text:
                    authors.append(name_elem.text)

            # Abstract/Summary
            summary_elem = entry.find("atom:summary", ARXIV_NS)
            abstract = (
                summary_elem.text.strip().replace("\n", " ")
                if summary_elem is not None and summary_elem.text
                else ""
            )

            # Categories
            categories = []
            for cat in entry.findall("arxiv:primary_category", ARXIV_NS):
                term = cat.get("term")
                if term:
                    categories.append(term)
            for cat in entry.findall("atom:category", ARXIV_NS):
                term = cat.get("term")
                if term and term not in categories:
                    categories.append(term)

            # Published date
            published_elem = entry.find("atom:published", ARXIV_NS)
            published = (
                published_elem.text
                if published_elem is not None and published_elem.text
                else ""
            )

            # PDF URL
            pdf_url = None
            for link in entry.findall("atom:link", ARXIV_NS):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href")
                    break
            if not pdf_url:
                pdf_url = f"http://arxiv.org/pdf/{paper_id}"

            results.append(
                {
                    "id": short_id,
                    "title": title,
                    "authors": authors,
                    "abstract": abstract,
                    "categories": categories,
                    "published": published,
                    "url": pdf_url,
                    "resource_uri": f"arxiv://{short_id}",
                }
            )

    except ET.ParseError as e:
        logger.error(f"Failed to parse arXiv XML response: {e}")
        raise ValueError(f"Failed to parse arXiv API response: {e}")

    return results


search_tool = types.Tool(
    name="search_papers",
    description="""Search for papers on arXiv with advanced filtering and query optimization.

QUERY CONSTRUCTION GUIDELINES:
- Use QUOTED PHRASES for exact matches: "multi-agent systems", "neural networks", "machine learning"
- Combine related concepts with OR: "AI agents" OR "software agents" OR "intelligent agents"  
- Use field-specific searches for precision:
  - ti:"exact title phrase" - search in titles only
  - au:"author name" - search by author
  - abs:"keyword" - search in abstracts only
- Use ANDNOT to exclude unwanted results: "machine learning" ANDNOT "survey"
- For best results, use 2-4 core concepts rather than long keyword lists

ADVANCED SEARCH PATTERNS:
- Field + phrase: ti:"transformer architecture" for papers with exact title phrase
- Multiple fields: au:"Smith" AND ti:"quantum" for author Smith's quantum papers  
- Exclusions: "deep learning" ANDNOT ("survey" OR "review") to exclude survey papers
- Broad + narrow: "artificial intelligence" AND (robotics OR "computer vision")

CATEGORY FILTERING (highly recommended for relevance):
- cs.AI: Artificial Intelligence
- cs.MA: Multi-Agent Systems  
- cs.LG: Machine Learning
- cs.CL: Computation and Language (NLP)
- cs.CV: Computer Vision
- cs.RO: Robotics
- cs.HC: Human-Computer Interaction
- cs.CR: Cryptography and Security
- cs.DB: Databases

EXAMPLES OF EFFECTIVE QUERIES:
- ti:"reinforcement learning" with categories: ["cs.LG", "cs.AI"] - for RL papers by title
- au:"Hinton" AND "deep learning" with categories: ["cs.LG"] - for Hinton's deep learning work
- "multi-agent" ANDNOT "survey" with categories: ["cs.MA"] - exclude survey papers
- abs:"transformer" AND ti:"attention" with categories: ["cs.CL"] - attention papers with transformer abstracts

DATE FILTERING: Use YYYY-MM-DD format for historical research:
- date_to: "2015-12-31" - for foundational/classic work (pre-2016)
- date_from: "2020-01-01" - for recent developments (post-2020)
- Both together for specific time periods

RESULT QUALITY: Results sorted by RELEVANCE (most relevant papers first), not just newest papers.
This ensures you get the most pertinent results regardless of publication date.

TIPS FOR FOUNDATIONAL RESEARCH:
- Use date_to: "2010-12-31" to find classic papers on BDI, SOAR, ACT-R
- Combine with field searches: ti:"BDI" AND abs:"belief desire intention"  
- Try author searches: au:"Rao" AND "BDI" for Anand Rao's foundational BDI work""",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": 'Search query using quoted phrases for exact matches (e.g., \'"machine learning" OR "deep learning"\') or specific technical terms. Avoid overly broad or generic terms.',
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 10, max: 50). Use 15-20 for comprehensive searches.",
            },
            "date_from": {
                "type": "string",
                "description": "Start date for papers (YYYY-MM-DD format). Use to find recent work, e.g., '2023-01-01' for last 2 years.",
            },
            "date_to": {
                "type": "string",
                "description": "End date for papers (YYYY-MM-DD format). Use with date_from to find historical work, e.g., '2020-12-31' for older research.",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Strongly recommended: arXiv categories to focus search (e.g., ['cs.AI', 'cs.MA'] for agent research, ['cs.LG'] for ML, ['cs.CL'] for NLP, ['cs.CV'] for vision). Greatly improves relevance.",
            },
            "sort_by": {
                "type": "string",
                "enum": ["relevance", "date"],
                "description": "Sort results by 'relevance' (most relevant first, default) or 'date' (newest first). Use 'relevance' for focused searches, 'date' for recent developments.",
            },
        },
        "required": ["query"],
    },
)


def _validate_categories(categories: List[str]) -> bool:
    """Validate that all provided categories are valid arXiv categories."""
    for category in categories:
        if "." in category:
            prefix = category.split(".")[0]
        else:
            prefix = category
        if prefix not in VALID_CATEGORIES:
            logger.warning(f"Unknown category prefix: {prefix}")
            return False
    return True


def _optimize_query(query: str) -> str:
    """Minimal query optimization - preserve user intent while fixing obvious issues."""

    # Don't modify queries with existing field specifiers (ti:, au:, abs:, cat:)
    if any(
        field in query
        for field in ["ti:", "au:", "abs:", "cat:", "AND", "OR", "ANDNOT"]
    ):
        logger.debug("Field-specific or boolean query detected - no optimization")
        return query

    # Don't modify queries that are already quoted
    if query.startswith('"') and query.endswith('"'):
        logger.debug("Pre-quoted query detected - no optimization")
        return query

    # For very long queries (>10 terms), suggest user be more specific rather than auto-converting
    terms = query.split()
    if len(terms) > 10:
        logger.warning(
            f"Very long query ({len(terms)} terms) - consider using quotes for phrases or field-specific searches"
        )

    # Only optimization: preserve the original query exactly as intended
    return query


def _process_paper(paper: arxiv.Result) -> Dict[str, Any]:
    """Process paper information with resource URI."""
    return {
        "id": paper.get_short_id(),
        "title": paper.title,
        "authors": [author.name for author in paper.authors],
        "abstract": paper.summary,
        "categories": paper.categories,
        "published": paper.published.isoformat(),
        "url": paper.pdf_url,
        "resource_uri": f"arxiv://{paper.get_short_id()}",
    }


async def handle_search(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle paper search requests with improved arXiv API integration.

    Uses raw HTTP requests when date filtering is requested to avoid URL encoding
    issues with the arxiv Python package. Falls back to the arxiv package for
    non-date queries for better compatibility.
    """
    try:
        max_results = min(int(arguments.get("max_results", 10)), settings.MAX_RESULTS)
        base_query = arguments["query"]
        date_from_arg = arguments.get("date_from")
        date_to_arg = arguments.get("date_to")
        categories = arguments.get("categories")
        sort_by_arg = arguments.get("sort_by", "relevance")

        logger.debug(
            f"Starting search with query: '{base_query}', max_results: {max_results}"
        )

        # Validate categories if provided
        if categories and not _validate_categories(categories):
            return [
                types.TextContent(
                    type="text",
                    text="Error: Invalid category provided. Please check arXiv category names.",
                )
            ]

        # Use raw HTTP API when date filtering is requested
        # This bypasses the arxiv package's URL encoding which breaks date syntax
        if date_from_arg or date_to_arg:
            logger.debug(
                f"Date filtering requested - using raw API: {date_from_arg} to {date_to_arg}"
            )

            try:
                optimized_query = (
                    _optimize_query(base_query) if base_query.strip() else ""
                )
                results = await _raw_arxiv_search(
                    query=optimized_query,
                    max_results=max_results,
                    sort_by=sort_by_arg,
                    date_from=date_from_arg,
                    date_to=date_to_arg,
                    categories=categories,
                )

                logger.info(
                    f"Raw API search completed: {len(results)} results returned"
                )
                response_data = {"total_results": len(results), "papers": results}

                return [
                    types.TextContent(
                        type="text", text=json.dumps(response_data, indent=2)
                    )
                ]

            except httpx.HTTPStatusError as e:
                logger.error(f"arXiv API HTTP error: {e}")
                return [
                    types.TextContent(
                        type="text", text=f"Error: arXiv API HTTP error - {str(e)}"
                    )
                ]
            except ValueError as e:
                return [types.TextContent(type="text", text=f"Error: {str(e)}")]

        # For non-date queries, use the arxiv package (more robust parsing)
        client = get_arxiv_client()

        # Build query components
        query_parts = []

        # Add base query with optimization
        if base_query.strip():
            optimized_query = _optimize_query(base_query)
            query_parts.append(f"({optimized_query})")
            if optimized_query != base_query:
                logger.debug(f"Optimized query: '{base_query}' -> '{optimized_query}'")

        # Add category filtering
        if categories:
            category_filter = " OR ".join(f"cat:{cat}" for cat in categories)
            query_parts.append(f"({category_filter})")
            logger.debug(f"Added category filter: {category_filter}")

        # Combine query parts
        if not query_parts:
            return [
                types.TextContent(
                    type="text", text="Error: No search criteria provided"
                )
            ]

        # Combine query parts - arXiv uses space for AND by default
        final_query = " ".join(query_parts)
        logger.debug(f"Final arXiv query: {final_query}")

        # Determine sort method
        if sort_by_arg == "date":
            sort_criterion = arxiv.SortCriterion.SubmittedDate
            logger.debug("Using date sorting (newest first)")
        else:
            sort_criterion = arxiv.SortCriterion.Relevance
            logger.debug("Using relevance sorting (most relevant first)")

        search = arxiv.Search(
            query=final_query,
            max_results=max_results,
            sort_by=sort_criterion,
        )

        # Process results
        results = []
        for paper in client.results(search):
            if len(results) >= max_results:
                break
            results.append(_process_paper(paper))

        logger.info(f"Search completed: {len(results)} results returned")
        response_data = {"total_results": len(results), "papers": results}

        return [
            types.TextContent(type="text", text=json.dumps(response_data, indent=2))
        ]

    except arxiv.ArxivError as e:
        logger.error(f"ArXiv API error: {e}")
        return [
            types.TextContent(type="text", text=f"Error: ArXiv API error - {str(e)}")
        ]
    except Exception as e:
        logger.error(f"Unexpected search error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
