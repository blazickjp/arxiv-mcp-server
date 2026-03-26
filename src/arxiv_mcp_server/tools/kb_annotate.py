"""Annotate knowledge base papers with notes, tags, reading status, and collections."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..store.knowledge_base import KnowledgeBase

logger = logging.getLogger("arxiv-mcp-server")

kb_annotate_tool = types.Tool(
    name="kb_annotate",
    description="""Add or update annotations on a paper in your knowledge base.

Set notes, tags, reading status, and manage collection membership in a single call.
Supports both replacing all tags and incremental add/remove operations.

EXAMPLES:
- Add reading notes: paper_id="2401.12345", notes="Key insight: combines RL with LLM planning"
- Tag a paper: paper_id="2401.12345", add_tags=["reinforcement-learning", "agents"]
- Mark as completed: paper_id="2401.12345", reading_status="completed"
- Add to collection: paper_id="2401.12345", add_to_collection="thesis-references"
- Combined: paper_id="2401.12345", notes="...", add_tags=["rl"], reading_status="reading"
""",
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "ID of the paper in the knowledge base.",
            },
            "notes": {
                "type": "string",
                "description": "Set or replace notes (personal annotations, key findings, etc.).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replace all tags with this list. Cannot be used with add_tags/remove_tags.",
            },
            "add_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Add these tags to the existing set.",
            },
            "remove_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Remove these tags from the existing set.",
            },
            "reading_status": {
                "type": "string",
                "enum": ["unread", "reading", "completed", "archived"],
                "description": "Set the reading status.",
            },
            "add_to_collection": {
                "type": "string",
                "description": "Add the paper to this collection (creates the collection if it does not exist).",
            },
            "remove_from_collection": {
                "type": "string",
                "description": "Remove the paper from this collection.",
            },
        },
        "required": ["paper_id"],
    },
)


async def handle_kb_annotate(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle annotation updates for a knowledge base paper."""
    try:
        kb = KnowledgeBase()
        paper_id = arguments["paper_id"]

        # 1. Validate paper exists
        paper = await kb.get_paper(paper_id)
        if paper is None:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": f"Paper '{paper_id}' not found in knowledge base.",
                            "hint": "Use kb_list or kb_search to find the correct paper ID, "
                            "or kb_save to add the paper first.",
                        },
                        indent=2,
                    ),
                )
            ]

        # 2. Build annotation kwargs
        annotate_kwargs: Dict[str, Any] = {}

        if "notes" in arguments:
            annotate_kwargs["notes"] = arguments["notes"]

        if "tags" in arguments:
            annotate_kwargs["tags"] = arguments["tags"]

        if "add_tags" in arguments:
            annotate_kwargs["add_tags"] = arguments["add_tags"]

        if "remove_tags" in arguments:
            annotate_kwargs["remove_tags"] = arguments["remove_tags"]

        if "reading_status" in arguments:
            annotate_kwargs["reading_status"] = arguments["reading_status"]

        # 3. Apply annotations if any
        if annotate_kwargs:
            result = await kb.annotate(paper_id, **annotate_kwargs)
            if result is None:
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(
                            {"error": f"Failed to annotate paper '{paper_id}'."},
                            indent=2,
                        ),
                    )
                ]

        # 4. Handle collection operations
        if "add_to_collection" in arguments:
            collection_name = arguments["add_to_collection"]
            await kb.create_collection(collection_name)
            await kb.add_to_collection(collection_name, paper_id)

        if "remove_from_collection" in arguments:
            collection_name = arguments["remove_from_collection"]
            removed = await kb.remove_from_collection(collection_name, paper_id)
            if not removed:
                logger.warning(
                    f"Paper '{paper_id}' was not in collection '{collection_name}'"
                )

        # 5. Return updated paper
        updated_paper = await kb.get_paper(paper_id)
        return [
            types.TextContent(
                type="text",
                text=json.dumps(updated_paper, indent=2),
            )
        ]

    except ValueError as e:
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"error": str(e)}, indent=2),
            )
        ]
    except Exception as e:
        logger.error(f"Unexpected error in kb_annotate: {e}")
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"error": f"Unexpected error: {str(e)}"}, indent=2),
            )
        ]
