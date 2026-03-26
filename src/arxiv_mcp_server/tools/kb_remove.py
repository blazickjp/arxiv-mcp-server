"""Remove a paper from the personal knowledge base."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..store.knowledge_base import KnowledgeBase

logger = logging.getLogger("arxiv-mcp-server")

kb_remove_tool = types.Tool(
    name="kb_remove",
    description=(
        "Remove a paper from the personal knowledge base. "
        "Requires explicit confirmation to prevent accidental deletions. "
        "Call with confirm=false first to preview what will be deleted."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "ID of the paper to remove.",
            },
            "confirm": {
                "type": "boolean",
                "description": (
                    "Must be true to actually delete. Set to false to preview "
                    "the paper details before confirming deletion."
                ),
            },
        },
        "required": ["paper_id", "confirm"],
    },
)


async def handle_kb_remove(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle removing a paper from the knowledge base.

    Args:
        arguments: Tool input matching the kb_remove schema.

    Returns:
        List with a single TextContent containing the result as JSON.
    """
    try:
        paper_id = arguments["paper_id"]
        confirm = arguments["confirm"]

        kb = KnowledgeBase()

        # Fetch the paper to show what's being deleted
        paper = await kb.get_paper(paper_id)
        if paper is None:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "not_found",
                            "message": f"No paper found with ID '{paper_id}'.",
                        },
                        indent=2,
                    ),
                )
            ]

        if not confirm:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "confirmation_required",
                            "message": (
                                "Paper found. Set confirm=true to delete it permanently."
                            ),
                            "paper": paper,
                        },
                        indent=2,
                    ),
                )
            ]

        # Confirmed — remove the paper
        removed = await kb.remove_paper(paper_id)

        if removed:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "removed",
                            "message": f"Paper '{paper.get('title', paper_id)}' has been removed from the knowledge base.",
                            "removed_paper": {
                                "id": paper.get("id"),
                                "title": paper.get("title"),
                                "source": paper.get("source"),
                            },
                        },
                        indent=2,
                    ),
                )
            ]
        else:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "message": f"Failed to remove paper '{paper_id}'. It may have already been deleted.",
                        },
                        indent=2,
                    ),
                )
            ]

    except Exception as e:
        logger.error(f"Unexpected error in kb_remove: {e}")
        return [types.TextContent(type="text", text=f"Error: {e}")]
