"""Research alert tools for watched topics."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.types import ToolAnnotations

from dateutil import parser

from ..config import Settings
from .search import (
    ArxivRateLimitError,
    _rate_limited_response,
    _raw_arxiv_search,
)

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

WATCH_FILE_NAME = "watched_topics.json"

watch_topic_tool = types.Tool(
    name="watch_topic",
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, openWorldHint=False
    ),
    description=(
        "Save or update a persistent research topic watch. "
        "When checked via check_alerts, returns only papers published since the last check — "
        "acting as a standing alert for new work on a topic. "
        "New watches seed last_checked to creation time so the first check does not dump historical matches. "
        "The topic string uses the same query syntax as search_papers (quoted phrases, field specifiers, boolean operators). "
        'Examples: \'"diffusion models" AND ti:"video generation"\', \'au:"LeCun" AND cs.LG\'. '
        "Calling watch_topic with the same topic string updates the existing watch rather than creating a duplicate. "
        "On update, omit categories to preserve existing filters; pass categories: [] to clear them. "
        "Pair with check_alerts to poll for new papers."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": (
                    "Query string to monitor. Uses arXiv search syntax — "
                    "quoted phrases for exact matches, field specifiers (ti:, au:, abs:), "
                    "and boolean operators (AND, OR, ANDNOT). "
                    'Example: \'"reinforcement learning" AND "robotics"\'.'
                ),
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional arXiv category filter (e.g. ['cs.LG', 'cs.AI']). "
                    "Narrows results to specific fields. "
                    "On update, omit this field to preserve existing categories; "
                    "pass an empty array [] to clear them."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum papers to return per alert check (default: 10).",
                "default": 10,
            },
        },
        "required": ["topic"],
        "additionalProperties": False,
    },
)

check_alerts_tool = types.Tool(
    name="check_alerts",
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=False, openWorldHint=True
    ),
    description=(
        "Check all saved topic watches for newly published papers since the last check. "
        "Omitting the topic parameter runs ALL saved watches and returns new papers for each. "
        "Passing a topic string checks only that specific watch. "
        "Advances each watch's drain cursor after running: when a page is truncated by "
        "max_results, has_more=true and check_start advances so later calls return the next "
        "papers in the same window (Atom date bounds alone are day-granular and would otherwise "
        "re-hit the boundary); last_checked tracks the newest returned paper. When the page is "
        "not full, last_checked becomes now and the drain cursor resets. "
        "Use watch_topic to register topics before calling this. "
        "Returns a clear not-found error if a topic is provided but no matching watch exists. "
        "Returns a summary with new paper counts, has_more, and full paper metadata per topic."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": (
                    "Optional: check only this specific watched topic (must match the topic string used in watch_topic exactly). "
                    "Omit to check all saved watches."
                ),
            }
        },
        "additionalProperties": False,
    },
)


list_watches_tool = types.Tool(
    name="list_watches",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    description=(
        "List all saved topic watches without checking for new papers. "
        "Returns each watch's topic, categories, last_checked timestamp, and other stored fields. "
        "Does not update last_checked — use this to inspect what is saved. "
        "Use unwatch_topic to remove a watch, or check_alerts to poll for new papers."
    ),
    inputSchema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)

unwatch_topic_tool = types.Tool(
    name="unwatch_topic",
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, openWorldHint=False
    ),
    description=(
        "Delete a saved topic watch by exact topic string. "
        "The topic must match the stored watch_topic value exactly. "
        "Returns a clear not-found error if no matching watch exists. "
        "Use list_watches to inspect saved watches before deleting."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": (
                    "Exact topic string of the watch to remove. "
                    "Must match the topic used in watch_topic."
                ),
            },
        },
        "required": ["topic"],
        "additionalProperties": False,
    },
)


def _watch_file_path() -> Path:
    """Get watched topics file path."""
    return Path(settings.STORAGE_PATH) / WATCH_FILE_NAME


def _load_watches() -> Dict[str, Any]:
    """Load watch storage from disk."""
    watch_file = _watch_file_path()
    if not watch_file.exists():
        return {"topics": []}

    try:
        return json.loads(watch_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Invalid watched topics file, resetting: %s", watch_file)
        return {"topics": []}


def _save_watches(payload: Dict[str, Any]) -> None:
    """Persist watches to disk."""
    _watch_file_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _now_iso() -> str:
    """UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _filter_by_topic(
    topics: List[Dict[str, Any]], topic_name: Optional[str]
) -> List[Dict[str, Any]]:
    """Filter watched topics by exact topic name if provided."""
    if not topic_name:
        return topics
    return [topic for topic in topics if topic.get("topic") == topic_name]


def _is_new_paper(published_value: str, last_checked: Optional[str]) -> bool:
    """Check if paper is newer than the last check timestamp."""
    if not last_checked:
        return True

    try:
        return parser.parse(published_value) > parser.parse(last_checked)
    except (ValueError, TypeError):
        return True


def _newest_published(papers: List[Dict[str, Any]]) -> Optional[str]:
    """Return the published timestamp of the newest paper in the list."""
    newest_value: Optional[str] = None
    newest_dt: Optional[datetime] = None
    for paper in papers:
        published = paper.get("published") or ""
        try:
            published_dt = parser.parse(published)
        except (ValueError, TypeError):
            continue
        if newest_dt is None or published_dt > newest_dt:
            newest_dt = published_dt
            newest_value = published
    return newest_value


def _page_is_truncated(
    returned: int,
    max_results: int,
    total_results: Optional[int],
    new_count: Optional[int] = None,
    start: int = 0,
) -> bool:
    """True when max_results or OpenSearch total indicates more papers remain.

    When OpenSearch reports a total, trust start+returned vs total. Otherwise a
    full page (returned/new_count == max_results) is treated as truncated so we
    never jump the watermark to wall-clock now and skip the rest of the window.
    """
    start = max(0, int(start or 0))
    if total_results is not None:
        return total_results > (start + returned)
    if returned >= max_results > 0:
        return True
    if new_count is not None and new_count >= max_results > 0:
        return True
    return False


def _clear_drain_cursor(topic: Dict[str, Any]) -> None:
    """Reset pagination fields used while draining a truncated watch window."""
    topic.pop("drain_from", None)
    topic.pop("check_start", None)


async def handle_watch_topic(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Save or update a watched topic definition."""
    try:
        topic = (arguments.get("topic") or "").strip()
        if not topic:
            return [types.TextContent(type="text", text="Error: topic is required")]

        max_results = min(int(arguments.get("max_results", 10)), settings.MAX_RESULTS)

        payload = _load_watches()
        topics = payload.get("topics", [])
        existing_index = next(
            (idx for idx, item in enumerate(topics) if item.get("topic") == topic), None
        )

        # On update, only replace categories when the key is present so omitted
        # categories preserve the stored filters. Explicit [] still clears.
        if "categories" in arguments:
            categories = arguments.get("categories") or []
        elif existing_index is not None:
            categories = topics[existing_index].get("categories") or []
        else:
            categories = []

        # Seed last_checked=now on create so the first check_alerts only
        # surfaces papers published after the watch was registered (#227).
        # Updates preserve the existing watermark (including an intentional None).
        now = _now_iso()
        record = {
            "topic": topic,
            "categories": categories,
            "max_results": max_results,
            "last_checked": now,
            "created_at": now,
            "updated_at": now,
        }

        if existing_index is not None:
            current = topics[existing_index]
            record["created_at"] = current.get("created_at", record["created_at"])
            record["last_checked"] = current.get("last_checked")
            topics[existing_index] = record
        else:
            topics.append(record)

        payload["topics"] = topics
        _save_watches(payload)

        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "message": "Topic watch saved",
                        "topic": record,
                    },
                    indent=2,
                ),
            )
        ]
    except Exception as exc:
        logger.error("watch_topic error: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]


async def handle_check_alerts(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Check all watched topics (or one topic) for newly published papers."""
    try:
        selected_topic = (arguments.get("topic") or "").strip() or None
        payload = _load_watches()
        all_topics = payload.get("topics", [])
        topics = _filter_by_topic(all_topics, selected_topic)

        # A specific topic that matches nothing must not look like "no new papers".
        if selected_topic is not None and not topics:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": f"Watch not found: {selected_topic}",
                        }
                    ),
                )
            ]

        now_iso = _now_iso()
        alerts: List[Dict[str, Any]] = []

        for topic in topics:
            topic_query = topic.get("topic", "")
            if not topic_query:
                continue

            last_checked = topic.get("last_checked")
            max_results = min(int(topic.get("max_results", 10)), settings.MAX_RESULTS)
            # Freeze the Atom date lower-bound for this drain. submittedDate is
            # day-granular, so bumping last_checked to a paper's published time
            # would re-fetch the same boundary hit at start=0. Paginate with
            # check_start against drain_from instead.
            drain_from = topic.get("drain_from")
            if drain_from is None:
                drain_from = last_checked
            check_start = max(0, int(topic.get("check_start") or 0))

            # Oldest-first so start offsets walk forward through the window.
            search_results, total_results = await _raw_arxiv_search(
                query=topic_query,
                max_results=max_results,
                sort_by="date",
                sort_order="ascending",
                date_from=drain_from,
                categories=topic.get("categories") or None,
                start=check_start,
            )

            new_papers = [
                paper
                for paper in search_results
                if _is_new_paper(paper.get("published", ""), last_checked)
            ]

            has_more = _page_is_truncated(
                len(search_results),
                max_results,
                total_results,
                new_count=len(new_papers),
                start=check_start,
            )

            if has_more:
                if search_results:
                    # Advance start past this raw page so a day-granular Atom
                    # re-hit of the boundary paper cannot freeze the drain
                    # (empty new_papers + has_more + frozen cursor).
                    topic["drain_from"] = drain_from
                    topic["check_start"] = check_start + len(search_results)
                    if new_papers:
                        topic["last_checked"] = _newest_published(new_papers)
                else:
                    # has_more with zero raw hits cannot progress — stop.
                    has_more = False
                    topic["last_checked"] = now_iso
                    _clear_drain_cursor(topic)
            else:
                topic["last_checked"] = now_iso
                _clear_drain_cursor(topic)

            topic["updated_at"] = now_iso

            alerts.append(
                {
                    "topic": topic_query,
                    "last_checked": last_checked,
                    "new_paper_count": len(new_papers),
                    "new_papers": new_papers,
                    "has_more": has_more,
                }
            )

        payload["topics"] = all_topics
        _save_watches(payload)

        result = {
            "status": "success",
            "checked_topics": len(topics),
            "alerts": alerts,
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    except ArxivRateLimitError as exc:
        # Parity with search_papers (#238): structured rate_limited JSON, not bare Error:
        logger.warning("check_alerts rate limited after retries: %s", exc)
        return _rate_limited_response(
            str(exc),
            retry_after_seconds=exc.retry_after_seconds,
            status_code=exc.status_code,
        )
    except Exception as exc:
        logger.error("check_alerts error: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]


async def handle_list_watches(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Return saved watches without updating last_checked."""
    try:
        payload = _load_watches()
        watches = list(payload.get("topics", []))
        result = {
            "status": "success",
            "watch_count": len(watches),
            "watches": watches,
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as exc:
        logger.error("list_watches error: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]


async def handle_unwatch_topic(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Delete a watched topic by exact topic string."""
    try:
        topic = (arguments.get("topic") or "").strip()
        if not topic:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "error", "message": "topic is required"}
                    ),
                )
            ]

        payload = _load_watches()
        topics = payload.get("topics", [])
        remaining = [item for item in topics if item.get("topic") != topic]
        if len(remaining) == len(topics):
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": f"Watch not found: {topic}",
                        }
                    ),
                )
            ]

        payload["topics"] = remaining
        _save_watches(payload)

        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "message": "Topic watch removed",
                        "topic": topic,
                    },
                    indent=2,
                ),
            )
        ]
    except Exception as exc:
        logger.error("unwatch_topic error: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]
