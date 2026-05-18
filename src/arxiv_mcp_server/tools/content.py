"""Helpers for returning large paper content safely."""

from typing import Any


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
