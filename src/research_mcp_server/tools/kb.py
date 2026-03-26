"""Unified knowledge base tool — dispatches to save, search, list, annotate, remove handlers."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from .kb_save import handle_kb_save
from .kb_search import handle_kb_search
from .kb_list import handle_kb_list
from .kb_annotate import handle_kb_annotate
from .kb_remove import handle_kb_remove

logger = logging.getLogger("research-mcp-server")

kb_tool = types.Tool(
    name="kb",
    description=(
        "Unified knowledge base for managing your saved research papers. "
        "Choose an action:\n"
        "- 'save': Save a paper (from arXiv, DOI, or manual entry) with optional tags, notes, and collection.\n"
        "- 'search': Semantic/keyword/hybrid search across saved papers. Fully local, no API calls.\n"
        "- 'list': Browse saved papers with filters, sorting, pagination, and stats.\n"
        "- 'annotate': Update tags, notes, reading status, or collection membership on a saved paper.\n"
        "- 'remove': Permanently delete a paper (preview first with confirm=false)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["save", "search", "list", "annotate", "remove"],
                "description": "The operation to perform.",
            },
            # --- save ---
            "source": {
                "type": "string",
                "enum": ["arxiv", "doi", "manual"],
                "description": (
                    "Paper source (for 'save' action): 'arxiv' (auto-fetches metadata), "
                    "'doi', or 'manual'. Also used as a filter in 'list' action."
                ),
            },
            "source_id": {
                "type": "string",
                "description": (
                    "arXiv ID (e.g. '2401.12345') or DOI (for 'save' action). "
                    "Required when source is 'arxiv' or 'doi'."
                ),
            },
            "title": {
                "type": "string",
                "description": "Paper title (for 'save' action with source='manual'). Required for manual entry.",
            },
            "authors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of author names (for 'save' action with source='manual'). Required for manual entry.",
            },
            "abstract": {
                "type": "string",
                "description": "Paper abstract (for 'save' action).",
            },
            # --- search ---
            "query": {
                "type": "string",
                "description": "Natural language search query (for 'search' action). Required for search.",
                "minLength": 1,
            },
            "mode": {
                "type": "string",
                "enum": ["hybrid", "semantic", "keyword"],
                "description": "Search mode (for 'search' action): 'hybrid' (default, best results), 'semantic', or 'keyword'.",
                "default": "hybrid",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (for 'search' action, default: 10, max: 50).",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
            # --- list ---
            "sort_by": {
                "type": "string",
                "enum": ["added_at", "updated_at", "title", "published_date"],
                "description": "Sort field (for 'list' action, default: added_at).",
                "default": "added_at",
            },
            "sort_order": {
                "type": "string",
                "enum": ["asc", "desc"],
                "description": "Sort direction (for 'list' action, default: desc).",
                "default": "desc",
            },
            "limit": {
                "type": "integer",
                "description": "Max papers to return (for 'list' action, default: 20, max: 100).",
                "default": 20,
                "minimum": 1,
                "maximum": 100,
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N results for pagination (for 'list' action, default: 0).",
                "default": 0,
                "minimum": 0,
            },
            "show_stats": {
                "type": "boolean",
                "description": "Include KB statistics in response (for 'list' action, default: false).",
                "default": False,
            },
            # --- annotate ---
            "add_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags to add to existing set (for 'annotate' action).",
            },
            "remove_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags to remove from existing set (for 'annotate' action).",
            },
            "add_to_collection": {
                "type": "string",
                "description": "Add paper to this collection (for 'annotate' action; creates collection if needed).",
            },
            "remove_from_collection": {
                "type": "string",
                "description": "Remove paper from this collection (for 'annotate' action).",
            },
            # --- remove ---
            "confirm": {
                "type": "boolean",
                "description": (
                    "Confirm deletion (for 'remove' action). "
                    "Set false to preview, true to permanently delete."
                ),
            },
            # --- shared across multiple actions ---
            "paper_id": {
                "type": "string",
                "description": (
                    "Paper ID in the knowledge base (for 'annotate' and 'remove' actions). "
                    "Use 'search' or 'list' to find IDs."
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "User-defined tags. For 'save': initial tags. For 'search'/'list': filter by tags. "
                    "For 'annotate': replace all tags (cannot combine with add_tags/remove_tags)."
                ),
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subject categories, e.g. ['cs.AI', 'cs.LG'] (for 'save', 'search', 'list').",
            },
            "reading_status": {
                "type": "string",
                "enum": ["unread", "reading", "completed", "archived"],
                "description": (
                    "Reading status. For 'save': initial status (default: unread). "
                    "For 'search'/'list': filter by status. For 'annotate': set new status."
                ),
            },
            "notes": {
                "type": "string",
                "description": "Personal notes (for 'save' or 'annotate' actions).",
            },
            "collection": {
                "type": "string",
                "description": (
                    "Collection name. For 'save': add paper to collection after saving. "
                    "For 'search'/'list': filter by collection."
                ),
            },
        },
        "required": ["action"],
    },
)


async def handle_kb(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Dispatch to the appropriate KB handler based on the 'action' field.

    Args:
        arguments: Tool input matching the kb schema. Must include 'action'.

    Returns:
        List with a single TextContent containing the result.
    """
    action = arguments.get("action")
    if not action:
        return [types.TextContent(type="text", text="Error: 'action' is required.")]

    if action == "save":
        return await handle_kb_save(arguments)
    elif action == "search":
        return await handle_kb_search(arguments)
    elif action == "list":
        return await handle_kb_list(arguments)
    elif action == "annotate":
        return await handle_kb_annotate(arguments)
    elif action == "remove":
        return await handle_kb_remove(arguments)
    else:
        return [
            types.TextContent(
                type="text",
                text=f"Error: Unknown action '{action}'. Use: save, search, list, annotate, remove.",
            )
        ]
