"""Search functionality for the arXiv MCP server."""

import json
import logging
import httpx
import asyncio
import xml.etree.ElementTree as ET
from urllib.parse import quote
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from dateutil import parser
import mcp.types as types
from mcp.types import ToolAnnotations
from ..config import Settings
from ..arxiv_api import ARXIV_RATE_LIMITER
from .content import CONTENT_WARNING

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

# Tool defaults (#128): compact pages by default.
DEFAULT_MAX_RESULTS = 5
DEFAULT_ABSTRACT_MODE = "snippet"
ABSTRACT_SNIPPET_CHARS = 280
ABSTRACT_MODES = ("none", "snippet", "full")
SORT_BY_VALUES = ("relevance", "date")
_ABSTRACT_TRUNCATION_MARK = "… [truncated]"

ARXIV_HEADERS = {
    "User-Agent": (
        f"{settings.APP_NAME}/{settings.APP_VERSION} "
        "(https://github.com/blazickjp/arxiv-mcp-server; research tool)"
    )
}


async def _rate_limited_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Make an HTTP request through the process-wide arXiv request gate."""

    async def request() -> httpx.Response:
        for attempt in range(2):  # one retry on timeout only
            try:
                response = await client.get(url, headers=ARXIV_HEADERS)
                if response.status_code in (429, 503):
                    logger.warning(
                        "arXiv rate limited (%s); not retrying", response.status_code
                    )
                    raise RuntimeError(
                        f"arXiv is rate limiting this IP (HTTP {response.status_code}). "
                        "Please wait 60 seconds before retrying."
                    )
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                if attempt == 0:
                    logger.warning("arXiv request timed out, retrying once")
                    await asyncio.sleep(5.0)
                else:
                    raise
        raise RuntimeError("arXiv request timed out after retry")

    return await ARXIV_RATE_LIMITER.run_async(request)


# arXiv API endpoint for raw queries (bypasses arxiv package URL encoding issues)
# Use HTTPS to avoid redirect from http -> https
ARXIV_API_URL = "https://export.arxiv.org/api/query"

# XML namespaces used in arXiv Atom feed
ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
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


_USER_QUERY_FIELD_PREFIXES = (
    "ti:",
    "au:",
    "abs:",
    "cat:",
    "co:",
    "jr:",
    "rn:",
    "all:",
)


def _user_query_has_field_prefix(query: str) -> bool:
    """Return True if the user already targeted an arXiv search field."""
    lowered = query.lower()
    return any(prefix in lowered for prefix in _USER_QUERY_FIELD_PREFIXES)


def _scope_user_query(query: str) -> str:
    """Group a user query and keep unprefixed terms in title/abstract.

    Bare tokens are mapped by arXiv to ``all:``, which includes authors. A
    short ``OR`` term such as ``MoE`` then matches author-name fragments
    (for example ``Moe Jafari`` or ``Mo Zhou``). Under date sort that
    collapses to "newest papers in these categories."
    """
    query = query.strip()
    if not query:
        return query
    if _user_query_has_field_prefix(query):
        return f"({query})"
    return f"(ti:({query}) OR abs:({query}))"


def _format_submitted_date_filter(
    date_from: Optional[str], date_to: Optional[str]
) -> str:
    """Build an arXiv submittedDate range using spaces around TO."""
    try:
        if date_from:
            start_date = parser.parse(date_from).strftime("%Y%m%d0000")
        else:
            start_date = "199107010000"  # arXiv started July 1991

        if date_to:
            end_date = parser.parse(date_to).strftime("%Y%m%d2359")
        else:
            end_date = datetime.now().strftime("%Y%m%d2359")
    except (ValueError, TypeError) as e:
        logger.error(f"Error parsing dates: {e}")
        raise ValueError(f"Invalid date format. Use YYYY-MM-DD format: {e}")

    # Spaces, not pre-baked '+TO+'. Percent-encoding then yields %20TO%20,
    # which decodes to the required range operator. Encoding a literal '+'
    # as %2B is what breaks the arxiv package date filter.
    return f"submittedDate:[{start_date} TO {end_date}]"


def build_arxiv_search_query(
    query: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    categories: Optional[List[str]] = None,
) -> str:
    """Return the unencoded arXiv search_query string."""
    query_parts: list[str] = []

    if query.strip():
        query_parts.append(_scope_user_query(query))

    if categories:
        category_filter = " OR ".join(f"cat:{cat}" for cat in categories)
        query_parts.append(f"({category_filter})")

    if date_from or date_to:
        date_filter = _format_submitted_date_filter(date_from, date_to)
        query_parts.append(date_filter)
        logger.debug(f"Added date filter: {date_filter}")

    if not query_parts:
        raise ValueError("No search criteria provided")

    return " AND ".join(query_parts)


def build_arxiv_search_url(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    sort_by: str = "relevance",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    categories: Optional[List[str]] = None,
    start: int = 0,
    sort_order: str = "descending",
) -> str:
    """Build a raw arXiv API URL with a percent-encoded search_query."""
    final_query = build_arxiv_search_query(
        query,
        date_from=date_from,
        date_to=date_to,
        categories=categories,
    )
    logger.debug(f"Raw API query: {final_query}")

    sort_map = {
        "relevance": "relevance",
        "date": "submittedDate",
    }
    order = sort_order if sort_order in ("ascending", "descending") else "descending"
    encoded_query = quote(final_query, safe="")
    start = max(0, int(start))
    base_params = (
        f"start={start}"
        f"&max_results={max_results}"
        f"&sortBy={sort_map.get(sort_by, 'relevance')}"
        f"&sortOrder={order}"
    )
    return f"{ARXIV_API_URL}?search_query={encoded_query}&{base_params}"


async def _raw_arxiv_search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    sort_by: str = "relevance",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    categories: Optional[List[str]] = None,
    start: int = 0,
    sort_order: str = "descending",
) -> tuple[List[Dict[str, Any]], Optional[int]]:
    """
    Perform arXiv search using raw HTTP requests.

    This bypasses the arxiv Python package to avoid URL encoding issues
    with date filters. The arxiv package encodes '+' as '%2B' which breaks
    the submittedDate:[YYYYMMDD TO YYYYMMDD] syntax.

    Returns (papers, total_results) where total_results is the OpenSearch
    corpus hit count from the Atom feed, not the page size.
    """
    url = build_arxiv_search_url(
        query,
        max_results=max_results,
        sort_by=sort_by,
        date_from=date_from,
        date_to=date_to,
        categories=categories,
        start=start,
        sort_order=sort_order,
    )
    logger.debug(f"Raw API URL: {url}")

    # Make the request via rate-limited helper
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await _rate_limited_get(client, url)

    papers = _parse_arxiv_atom_response(response.text)
    return papers, _parse_opensearch_total_results(response.text)


def _parse_opensearch_total_results(xml_text: str) -> Optional[int]:
    """Return arXiv OpenSearch totalResults, or None if the feed omits it."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    elem = root.find("opensearch:totalResults", ARXIV_NS)
    if elem is None:
        # Some feeds use a different prefix or the 1.0 namespace.
        for child in list(root):
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "totalResults":
                elem = child
                break
    if elem is None or elem.text is None:
        return None
    try:
        return int(elem.text.strip())
    except ValueError:
        return None


def _normalize_abstract_mode(value: Any) -> str:
    """Return a valid abstract_mode or raise ValueError."""
    if value is None:
        return DEFAULT_ABSTRACT_MODE
    if not isinstance(value, str):
        raise ValueError(
            "abstract_mode must be one of: none, snippet, full "
            f"(got {type(value).__name__})"
        )
    mode = value.strip().lower()
    if mode not in ABSTRACT_MODES:
        raise ValueError(
            f"abstract_mode must be one of: none, snippet, full (got {value!r})"
        )
    return mode


def _normalize_sort_by(value: Any) -> str:
    """Return a valid sort_by or raise ValueError.

    Only the documented values ``relevance`` and ``date`` are accepted.
    Aliases such as ``submittedDate`` are intentionally rejected (HOLD).
    """
    if value is None:
        return "relevance"
    if not isinstance(value, str):
        allowed = ", ".join(SORT_BY_VALUES)
        raise ValueError(f"Invalid sort_by value {value!r}. Allowed values: {allowed}")
    sort_by = value.strip().lower()
    if sort_by not in SORT_BY_VALUES:
        allowed = ", ".join(SORT_BY_VALUES)
        raise ValueError(f"Invalid sort_by value {value!r}. Allowed values: {allowed}")
    return sort_by


def _snippet_abstract(abstract: str, max_chars: int = ABSTRACT_SNIPPET_CHARS) -> str:
    """Return a deterministic bounded abstract snippet, marked when truncated."""
    if len(abstract) <= max_chars:
        return abstract

    # Fixed-length slice (no word-boundary heuristics) for stable output.
    return abstract[:max_chars].rstrip() + _ABSTRACT_TRUNCATION_MARK


def _apply_abstract_mode(
    papers: List[Dict[str, Any]], abstract_mode: str
) -> List[Dict[str, Any]]:
    """Project paper dicts according to abstract_mode without mutating inputs."""
    mode = _normalize_abstract_mode(abstract_mode)
    projected: List[Dict[str, Any]] = []
    for paper in papers:
        item = dict(paper)
        if mode == "none":
            item.pop("abstract", None)
        elif mode == "snippet":
            abstract = item.get("abstract")
            if isinstance(abstract, str):
                item["abstract"] = _snippet_abstract(abstract)
        # mode == "full": keep abstract as parsed
        projected.append(item)
    return projected


def _build_search_response(
    papers: List[Dict[str, Any]],
    *,
    total_results: Optional[int] = None,
    has_more: Optional[bool] = None,
    start: int = 0,
    abstract_mode: str = DEFAULT_ABSTRACT_MODE,
) -> Dict[str, Any]:
    """Assemble search JSON. Never report page size as total_results.

    Pagination mirrors read_paper / content helpers: ``start`` is the
    zero-based offset for this page and ``next_start`` is set when more
    results remain (otherwise None). ``abstract_mode`` is echoed so
    clients can continue pagination with the same projection.
    """
    returned = len(papers)
    start = max(0, int(start))
    mode = _normalize_abstract_mode(abstract_mode)
    response: Dict[str, Any] = {
        "returned": returned,
        "start": start,
        "abstract_mode": mode,
        "papers": papers,
    }
    # Once per response (#230): avoid repeating EXTERNAL CONTENT on every abstract.
    if mode != "none":
        response["content_warning"] = CONTENT_WARNING
    if total_results is not None:
        response["total_results"] = total_results
        if has_more is None:
            has_more = total_results > (start + returned)
    if has_more is not None:
        response["has_more"] = bool(has_more)
    if response.get("has_more"):
        response["next_start"] = start + returned
    else:
        response["next_start"] = None
    return response


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
                    "versioned_id": paper_id,
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
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "Search arXiv by query with optional categories, date range, sort, and pagination.\n\n"
        "Query: prefer quoted phrases; ti:/au:/abs:/cat:; AND/OR/ANDNOT. "
        "Unprefixed terms match title+abstract (not authors). "
        "Use categories (cs.AI, cs.LG, cs.CL, cs.CV, cs.MA, cs.RO, stat.ML, quant-ph). "
        "Catalog/examples: README 'search_papers query guide'.\n\n"
        "Dates YYYY-MM-DD (date_from/date_to). sort_by relevance|date. "
        "max_results default 5 (cap 50). abstract_mode none|snippet|full (default snippet). "
        "start default 0; response: total_results, returned, has_more, next_start, "
        "abstract_mode. Pass next_start with same abstract_mode. "
        "Use get_abstract after compact search — not after abstract_mode=full.\n\n"
        "arXiv ~3s between requests (server-side). On rate-limit wait ~60s."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "arXiv query string. Prefer quoted phrases and ti:/au:/abs:/cat: "
                    "field prefixes; AND/OR/ANDNOT supported."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (default: 5, max: 50).",
            },
            "start": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Zero-based result offset (default: 0). Pass next_start from a "
                    "previous response to fetch the next page."
                ),
            },
            "abstract_mode": {
                "type": "string",
                "enum": ["none", "snippet", "full"],
                "description": (
                    "Abstract projection (default snippet ~280 chars, marked if "
                    "truncated; full=complete; none=omit)."
                ),
            },
            "date_from": {
                "type": "string",
                "description": "Inclusive start date (YYYY-MM-DD).",
            },
            "date_to": {
                "type": "string",
                "description": "Inclusive end date (YYYY-MM-DD).",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "arXiv category filters (e.g. ['cs.LG', 'cs.AI']). Strongly "
                    "improves relevance."
                ),
            },
            "sort_by": {
                "type": "string",
                "enum": ["relevance", "date"],
                "description": (
                    "Sort by 'relevance' (default) or 'date' (newest first)."
                ),
            },
        },
        "required": ["query"],
        "additionalProperties": False,
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


async def handle_search(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle paper search requests via the arXiv Atom API.

    Always uses raw HTTP so OpenSearch ``totalResults`` (corpus hit count)
    is available for every query — including those without date filters
    (see #189). Raw requests also avoid the arxiv package URL-encoding
    bug that breaks ``submittedDate`` ranges.
    """
    try:
        max_results = min(
            int(arguments.get("max_results", DEFAULT_MAX_RESULTS)),
            settings.MAX_RESULTS,
        )
        start_arg = arguments.get("start", 0)
        try:
            start = max(0, int(start_arg))
        except (TypeError, ValueError):
            start = 0
        try:
            abstract_mode = _normalize_abstract_mode(
                arguments.get("abstract_mode", DEFAULT_ABSTRACT_MODE)
            )
        except ValueError as e:
            return [types.TextContent(type="text", text=f"Error: {str(e)}")]
        base_query = arguments["query"]
        date_from_arg = arguments.get("date_from")
        date_to_arg = arguments.get("date_to")
        categories = arguments.get("categories")
        try:
            sort_by_arg = _normalize_sort_by(arguments.get("sort_by", "relevance"))
        except ValueError as e:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({"status": "error", "message": str(e)}),
                )
            ]

        logger.debug(
            "Starting search with query: %r, max_results: %s, start: %s, "
            "abstract_mode: %s",
            base_query,
            max_results,
            start,
            abstract_mode,
        )

        # Validate categories if provided
        if categories and not _validate_categories(categories):
            return [
                types.TextContent(
                    type="text",
                    text="Error: Invalid category provided. Please check arXiv category names.",
                )
            ]

        try:
            optimized_query = _optimize_query(base_query) if base_query.strip() else ""
            results, total_results = await _raw_arxiv_search(
                query=optimized_query,
                max_results=max_results,
                sort_by=sort_by_arg,
                date_from=date_from_arg,
                date_to=date_to_arg,
                categories=categories,
                start=start,
            )
            results = _apply_abstract_mode(results, abstract_mode)

            logger.info(f"Search completed: {len(results)} results returned")
            response_data = _build_search_response(
                results,
                total_results=total_results,
                start=start,
                abstract_mode=abstract_mode,
            )

            return [
                types.TextContent(type="text", text=json.dumps(response_data, indent=2))
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

    except Exception as e:
        logger.error(f"Unexpected search error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
