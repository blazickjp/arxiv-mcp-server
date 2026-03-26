"""Thin wrapper over the arXiv API for advanced query building.

Builds structured arXiv query strings from typed parameters and
handles the raw HTTP search (reusing upstream search.py patterns).
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..tools.search import _raw_arxiv_search
from ..utils.rate_limiter import arxiv_limiter

logger = logging.getLogger("arxiv-mcp-server")


@dataclass(frozen=True)
class QueryField:
    """A single field-specific search term."""

    prefix: str  # ti, au, abs, all, cat, co, jr, rn
    value: str

    def to_query(self) -> str:
        """Convert to arXiv query syntax.

        Note: arXiv's API does not reliably support quoted multi-word
        phrases with %22. Multi-word values are passed unquoted —
        the API treats all words after the prefix until the next
        boolean operator as part of the field search.
        """
        return f"{self.prefix}:{self.value}"


def build_query(
    *,
    title: Optional[str] = None,
    author: Optional[str] = None,
    abstract: Optional[str] = None,
    all_fields: Optional[str] = None,
    categories: Optional[list[str]] = None,
    exclude_terms: Optional[str] = None,
) -> str:
    """Build an arXiv query string from structured fields.

    Args:
        title: Search in paper titles (ti: prefix).
        author: Search by author name (au: prefix).
        abstract: Search in abstracts (abs: prefix).
        all_fields: Search across all fields (all: prefix).
        categories: arXiv category codes to filter by (OR'd).
        exclude_terms: Terms to exclude (ANDNOT).

    Returns:
        arXiv query string ready for the API.
    """
    parts: list[str] = []

    if title:
        parts.append(QueryField("ti", title).to_query())
    if author:
        parts.append(QueryField("au", author).to_query())
    if abstract:
        parts.append(QueryField("abs", abstract).to_query())
    if all_fields:
        parts.append(QueryField("all", all_fields).to_query())

    if categories:
        cat_parts = " OR ".join(f"cat:{c}" for c in categories)
        parts.append(f"({cat_parts})")

    query = " AND ".join(parts) if parts else ""

    if exclude_terms and query:
        exclude_words = exclude_terms.strip().split()
        for word in exclude_words:
            query += f" ANDNOT all:{word}"

    return query


async def advanced_search(
    *,
    title: Optional[str] = None,
    author: Optional[str] = None,
    abstract: Optional[str] = None,
    all_fields: Optional[str] = None,
    categories: Optional[list[str]] = None,
    exclude_terms: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    max_results: int = 10,
    sort_by: str = "relevance",
    sort_order: str = "descending",
) -> list[dict[str, Any]]:
    """Execute an advanced structured search against arXiv.

    Builds the query from typed fields, respects rate limiting,
    and returns parsed results.

    Args:
        title: Search in paper titles.
        author: Search by author.
        abstract: Search in abstracts.
        all_fields: Search all fields.
        categories: Category filter list.
        exclude_terms: Terms to exclude.
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        max_results: Max results (1-50).
        sort_by: Sort criterion.
        sort_order: Sort direction.

    Returns:
        List of paper result dicts.
    """
    query = build_query(
        title=title,
        author=author,
        abstract=abstract,
        all_fields=all_fields,
        categories=categories,
        exclude_terms=exclude_terms,
    )

    if not query and not (date_from or date_to):
        raise ValueError(
            "At least one search field (title, author, abstract, all_fields) "
            "or a date range must be provided."
        )

    await arxiv_limiter.wait()

    # Map sort_by to what the raw search expects
    sort_map = {"relevance": "relevance", "submittedDate": "date", "lastUpdatedDate": "date"}
    mapped_sort = sort_map.get(sort_by, sort_by)

    results = await _raw_arxiv_search(
        query=query if query else "",
        max_results=max_results,
        sort_by=mapped_sort,
        date_from=date_from,
        date_to=date_to,
        categories=categories if not query or "cat:" not in query else None,
    )

    return results
