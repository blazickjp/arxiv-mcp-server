"""Shared arXiv identifier normalization and validation.

Used by tools that accept a paper_id so ``arxiv:`` prefixes and common
abs/pdf URL wrappers are stripped before lookup, and invalid IDs never
reach the arXiv API.
"""

from __future__ import annotations

import re
from typing import Optional

# Matches both new-style (YYMM.NNNNN) and old-style (cat/YYMMNNN) arXiv IDs,
# with optional version suffix (v1, v2, …).
_ARXIV_ID_RE = re.compile(
    r"^(\d{4}\.\d{4,5}(v\d+)?"  # new-style: 2404.18922 or 2404.18922v3
    r"|[a-z\-]+(/[a-z\-]+)?/\d{7}(v\d+)?)$",  # old-style: hep-ph/9901234
    re.IGNORECASE,
)

_PREFIX_RE = re.compile(r"^arxiv:", re.IGNORECASE)

# Cheap wrappers: optional scheme, optional www, arxiv.org/(abs|pdf)/ID[.pdf]
_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:\.pdf)?/?$",
    re.IGNORECASE,
)

# Trailing version suffix on an otherwise-valid arXiv ID (v1, v7, …).
_VERSION_SUFFIX_RE = re.compile(r"(v\d+)$", re.IGNORECASE)


def is_valid_arxiv_id(stem: str) -> bool:
    """Return True if *stem* looks like a valid arXiv paper ID."""
    return bool(_ARXIV_ID_RE.match(stem))


def normalize_arxiv_id(raw: str) -> str:
    """Strip whitespace, ``arxiv:`` prefixes, and common abs/pdf URL wrappers.

    Version suffixes (e.g. ``v7``) are preserved. Invalid input is returned
    in best-effort stripped form so callers can validate separately.
    """
    value = raw.strip()
    url_match = _URL_RE.match(value)
    if url_match:
        value = url_match.group(1).strip()
    # Query/fragment may remain if the caller passed a bare ID?query — drop them.
    value = value.split("?", 1)[0].split("#", 1)[0].strip()
    value = _PREFIX_RE.sub("", value).strip()
    return value


def parse_arxiv_id(raw: str) -> Optional[str]:
    """Normalize *raw* and return it only if it is a valid arXiv ID."""
    if not isinstance(raw, str):
        return None
    normalized = normalize_arxiv_id(raw)
    if not normalized or not is_valid_arxiv_id(normalized):
        return None
    return normalized


def arxiv_version_suffix(paper_id: str) -> Optional[str]:
    """Return the trailing ``vN`` suffix (lowercased), or None if absent."""
    match = _VERSION_SUFFIX_RE.search(paper_id)
    if not match:
        return None
    return match.group(1).lower()


def bare_arxiv_id(paper_id: str) -> str:
    """Strip a trailing version suffix, keeping the bare arXiv identifier."""
    return _VERSION_SUFFIX_RE.sub("", paper_id)


def arxiv_version_number(paper_id: str) -> int:
    """Numeric version for ordering; bare IDs sort as ``-1`` (below v1)."""
    suffix = arxiv_version_suffix(paper_id)
    if suffix is None:
        return -1
    return int(suffix[1:])
