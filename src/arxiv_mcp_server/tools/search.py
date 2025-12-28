"""Search functionality for the arXiv MCP server."""

import arxiv
import json
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone
from dateutil import parser
import mcp.types as types
from mcp.types import ToolAnnotations
from ..config import Settings

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

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

search_tool = types.Tool(
    name="search_papers",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
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


def _build_date_filter(date_from: str = None, date_to: str = None) -> str:
    """Build arXiv API date filter using submittedDate syntax."""
    if not date_from and not date_to:
        return ""

    try:
        # Parse and format dates for arXiv API (YYYYMMDDTTTT format where TTTT is time to minute)
        if date_from:
            start_date = parser.parse(date_from).strftime("%Y%m%d0000")
        else:
            start_date = "199107010000"  # arXiv started July 1991

        if date_to:
            end_date = parser.parse(date_to).strftime("%Y%m%d2359")
        else:
            end_date = datetime.now().strftime("%Y%m%d2359")

        return f"submittedDate:[{start_date}+TO+{end_date}]"
    except (ValueError, TypeError) as e:
        logger.error(f"Error parsing dates: {e}")
        raise ValueError(f"Invalid date format. Use YYYY-MM-DD format: {e}")


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
    """Handle paper search requests with improved arXiv API integration."""
    try:
        client = arxiv.Client()
        max_results = min(int(arguments.get("max_results", 10)), settings.MAX_RESULTS)
        base_query = arguments["query"]

        logger.debug(
            f"Starting search with query: '{base_query}', max_results: {max_results}"
        )

        # Build query components
        query_parts = []

        # Add base query with optimization
        if base_query.strip():
            optimized_query = _optimize_query(base_query)
            query_parts.append(f"({optimized_query})")
            if optimized_query != base_query:
                logger.debug(f"Optimized query: '{base_query}' -> '{optimized_query}'")

        # Add category filtering
        if categories := arguments.get("categories"):
            if not _validate_categories(categories):
                return [
                    types.TextContent(
                        type="text",
                        text="Error: Invalid category provided. Please check arXiv category names.",
                    )
                ]
            category_filter = " OR ".join(f"cat:{cat}" for cat in categories)
            query_parts.append(f"({category_filter})")
            logger.debug(f"Added category filter: {category_filter}")

        # Add date filtering using arXiv API syntax
        # Temporarily disable server-side date filtering due to API issues
        # Will filter client-side for now
        date_from_arg = arguments.get("date_from")
        date_to_arg = arguments.get("date_to")
        if date_from_arg or date_to_arg:
            logger.debug(f"Date filtering requested: {date_from_arg} to {date_to_arg}")
            # We'll handle this client-side after getting results

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

        # Increase max_results slightly to account for any edge cases
        # but cap it to avoid overwhelming the API
        api_max_results = min(max_results + 5, settings.MAX_RESULTS)

        # Determine sort method
        sort_by_arg = arguments.get("sort_by", "relevance")
        if sort_by_arg == "date":
            sort_criterion = arxiv.SortCriterion.SubmittedDate
            logger.debug("Using date sorting (newest first)")
        else:
            sort_criterion = arxiv.SortCriterion.Relevance
            logger.debug("Using relevance sorting (most relevant first)")

        search = arxiv.Search(
            query=final_query,
            max_results=api_max_results,
            sort_by=sort_criterion,
        )

        # Process results with client-side date filtering
        results = []
        result_count = 0

        # Parse date filters if provided
        date_from_parsed = None
        date_to_parsed = None
        if date_from_arg:
            try:
                date_from_parsed = parser.parse(date_from_arg).replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, TypeError) as e:
                return [
                    types.TextContent(
                        type="text", text=f"Error: Invalid date_from format - {str(e)}"
                    )
                ]

        if date_to_arg:
            try:
                date_to_parsed = parser.parse(date_to_arg).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError) as e:
                return [
                    types.TextContent(
                        type="text", text=f"Error: Invalid date_to format - {str(e)}"
                    )
                ]

        for paper in client.results(search):
            if result_count >= max_results:
                break

            # Apply client-side date filtering
            paper_date = paper.published
            if not paper_date.tzinfo:
                paper_date = paper_date.replace(tzinfo=timezone.utc)

            if date_from_parsed and paper_date < date_from_parsed:
                continue
            if date_to_parsed and paper_date > date_to_parsed:
                continue

            results.append(_process_paper(paper))
            result_count += 1

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
