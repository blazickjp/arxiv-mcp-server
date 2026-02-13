"""Research alert tools for watched topics."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types
from dateutil import parser

from ..config import Settings
from .search import _raw_arxiv_search

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

WATCH_FILE_NAME = "watched_topics.json"

watch_topic_tool = types.Tool(
    name="watch_topic",
    description="Save or update a watched research topic for alerts.",
    inputSchema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic query string to monitor.",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional arXiv categories filter.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum papers to fetch per alert check.",
                "default": 10,
            },
        },
        "required": ["topic"],
    },
)

check_alerts_tool = types.Tool(
    name="check_alerts",
    description="Run saved topic watches and return new papers since last check.",
    inputSchema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Optional: check only this watched topic.",
            }
        },
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


def _filter_by_topic(topics: List[Dict[str, Any]], topic_name: Optional[str]) -> List[Dict[str, Any]]:
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


async def handle_watch_topic(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Save or update a watched topic definition."""
    try:
        topic = (arguments.get("topic") or "").strip()
        if not topic:
            return [types.TextContent(type="text", text="Error: topic is required")]

        categories = arguments.get("categories") or []
        max_results = min(int(arguments.get("max_results", 10)), settings.MAX_RESULTS)

        payload = _load_watches()
        topics = payload.get("topics", [])
        existing_index = next(
            (idx for idx, item in enumerate(topics) if item.get("topic") == topic), None
        )

        record = {
            "topic": topic,
            "categories": categories,
            "max_results": max_results,
            "last_checked": None,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
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

        now_iso = _now_iso()
        alerts: List[Dict[str, Any]] = []

        for topic in topics:
            topic_query = topic.get("topic", "")
            if not topic_query:
                continue

            last_checked = topic.get("last_checked")
            search_results = await _raw_arxiv_search(
                query=topic_query,
                max_results=min(int(topic.get("max_results", 10)), settings.MAX_RESULTS),
                sort_by="date",
                date_from=last_checked,
                categories=topic.get("categories") or None,
            )

            new_papers = [
                paper
                for paper in search_results
                if _is_new_paper(paper.get("published", ""), last_checked)
            ]

            alerts.append(
                {
                    "topic": topic_query,
                    "last_checked": last_checked,
                    "new_paper_count": len(new_papers),
                    "new_papers": new_papers,
                }
            )

            topic["last_checked"] = now_iso
            topic["updated_at"] = now_iso

        payload["topics"] = all_topics
        _save_watches(payload)

        result = {
            "status": "success",
            "checked_topics": len(topics),
            "alerts": alerts,
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as exc:
        logger.error("check_alerts error: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]
