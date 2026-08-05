"""Helpers for returning large paper content safely."""

from typing import Any

# Matches the bound already used by latex.py's list/section tools (issue #127):
# unbounded default reads from download_paper/read_paper let one call return an
# entire paper (measured ~111K chars / ~27.8K tokens for one real paper), so
# every MCP client has to remember optional pagination arguments just to stay
# inside its own context window.
DEFAULT_MAX_CHARS = 12_000
MAX_RETURN_CHARS = 50_000


def _coerce_nonnegative_int(value: Any, default: int) -> int:
    """Return ``value`` as a non-negative integer, or ``default`` if absent."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _coerce_positive_int(value: Any) -> int | None:
    """Return ``value`` as a positive integer, or None if absent/invalid."""
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def bound_arguments(
    arguments: dict[str, Any], default_max_chars: int = DEFAULT_MAX_CHARS
) -> dict[str, Any]:
    """Return a copy of ``arguments`` with a bounded ``max_chars`` applied.

    Omitting ``max_chars`` used to mean "return the whole paper," which made
    efficient behavior depend on every caller remembering an optional
    argument. This applies the same bounded-by-default pattern already used
    by the LaTeX section tools: no explicit ``max_chars`` -> capped at
    ``default_max_chars``; an explicit value is clamped to
    ``MAX_RETURN_CHARS``. Passing ``return_full_text: true`` opts back into
    the old unbounded behavior for callers that need it.
    """
    bounded = dict(arguments)
    if bounded.get("return_full_text") is True:
        return bounded
    if "max_chars" not in bounded or bounded["max_chars"] is None:
        bounded["max_chars"] = default_max_chars
    else:
        try:
            bounded["max_chars"] = min(
                MAX_RETURN_CHARS, max(1, int(bounded["max_chars"]))
            )
        except (TypeError, ValueError):
            bounded["max_chars"] = default_max_chars
    return bounded


def paginate_content(content: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Slice paper content and report continuation metadata.

    MCP clients and model gateways often impose per-tool-output display/context
    caps. Returning explicit chunks lets callers retrieve complete papers without
    mistaking client-side truncation for a failed download.
    """
    content_length = len(content)
    start = min(_coerce_nonnegative_int(arguments.get("start"), 0), content_length)
    max_chars = _coerce_positive_int(arguments.get("max_chars"))

    end = (
        content_length if max_chars is None else min(content_length, start + max_chars)
    )
    chunk = content[start:end]
    is_truncated = end < content_length

    return {
        "content": chunk,
        "content_length": content_length,
        "start": start,
        "returned_chars": len(chunk),
        "next_start": end if is_truncated else None,
        "is_truncated": is_truncated,
    }


def add_content_payload(
    payload: dict[str, Any],
    content: str,
    arguments: dict[str, Any],
    content_warning: str,
) -> dict[str, Any]:
    """Add paginated content fields to a JSON response payload."""
    page = paginate_content(content, arguments)
    chunk = page.pop("content")
    payload.update(page)
    payload["content"] = content_warning + chunk
    return payload
