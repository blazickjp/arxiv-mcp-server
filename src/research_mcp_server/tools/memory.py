"""Unified research memory tool.

Merges session-scoped research context (papers, questions, findings) with
cross-session persistent memory (theses, digests, warm context) into a
single ``memory`` MCP tool.  Session tracking is delegated to
``handle_research_context``; persistent memory actions are delegated to
``handle_research_memory``.
"""

import logging
from typing import Any, Dict, List

import mcp.types as types

from .research_context import handle_research_context
from .research_memory_tools import handle_research_memory

logger = logging.getLogger("research-mcp-server")

# ---------------------------------------------------------------------------
# Action sets
# ---------------------------------------------------------------------------

SESSION_ACTIONS = {
    "create",
    "status",
    "log_paper",
    "add_question",
    "answer_question",
    "add_finding",
    "summarize",
    "list",
    "complete",
}

MEMORY_ACTIONS = {
    "add_thesis",
    "update_thesis",
    "list_theses",
    "save_digest",
    "warm_context",
}

ALL_ACTIONS = sorted(SESSION_ACTIONS | MEMORY_ACTIONS)

# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

memory_tool = types.Tool(
    name="memory",
    description=(
        "Persistent research memory and session tracking. Combines "
        "session-scoped context (papers, questions, findings) with "
        "cross-session persistent memory (theses, digests, warm context). "
        "Use 'warm_context' at the start of research to load accumulated "
        "knowledge. Use session actions to track current work. Use "
        "thesis/digest actions for cross-session persistence."
    ),
    inputSchema={
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "description": "The operation to perform.",
                "enum": ALL_ACTIONS,
            },
            # --- Session tracking fields (from research_context) ---
            "session_id": {
                "type": "string",
                "description": (
                    "Session identifier. Required for most session actions "
                    "except 'create' and 'list'. If omitted, uses the most "
                    "recent active session."
                ),
            },
            "name": {
                "type": "string",
                "description": "Session name (for 'create' action).",
            },
            "goal": {
                "type": "string",
                "description": "Research goal (for 'create' action).",
            },
            "paper_id": {
                "type": "string",
                "description": "Paper identifier, e.g. arXiv ID (for 'log_paper').",
            },
            "paper_action": {
                "type": "string",
                "description": "What was done with the paper (for 'log_paper').",
                "enum": ["searched", "read", "saved", "compared", "cited"],
            },
            "question": {
                "type": "string",
                "description": "Research question text (for 'add_question').",
            },
            "thread_id": {
                "type": "integer",
                "description": "Thread identifier (for 'answer_question').",
            },
            "answer": {
                "type": "string",
                "description": "Answer text (for 'answer_question').",
            },
            "finding": {
                "type": "string",
                "description": "Key finding text (for 'add_finding').",
            },
            "evidence_paper_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paper IDs supporting this finding (for 'add_finding').",
            },
            "notes": {
                "type": "string",
                "description": "Optional notes (for 'log_paper' and other actions).",
            },
            "status": {
                "type": "string",
                "description": "Filter by status (for 'list' action).",
            },
            # --- Persistent memory fields (from research_memory) ---
            "statement": {
                "type": "string",
                "description": "Thesis statement (for 'add_thesis').",
            },
            "category": {
                "type": "string",
                "enum": ["primary", "secondary", "exploratory"],
                "description": (
                    "Thesis category (for 'add_thesis'). Default: exploratory."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "Confidence score 0-1 (for 'add_thesis' and 'update_thesis')."
                ),
            },
            "thesis_id": {
                "type": "string",
                "description": "Thesis UUID (for 'update_thesis').",
            },
            "thesis_status": {
                "type": "string",
                "enum": ["active", "validated", "invalidated", "dormant"],
                "description": "Thesis status (for 'update_thesis').",
            },
            "evidence": {
                "type": "object",
                "description": "Evidence object to append (for 'update_thesis').",
            },
            "content": {
                "type": "string",
                "description": "Digest content text (for 'save_digest').",
            },
            "validated_theses": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of validated thesis descriptions (for 'save_digest')."
                ),
            },
            "emerging_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of emerging pattern descriptions (for 'save_digest')."
                ),
            },
        },
    },
)

# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def handle_memory(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Route memory tool calls to the appropriate handler.

    Session-scoped actions are forwarded to ``handle_research_context``.
    Persistent memory actions are forwarded to ``handle_research_memory``.

    Args:
        arguments: Tool input with ``action`` and action-specific parameters.

    Returns:
        List containing a single ``TextContent`` with JSON results.
    """
    action = arguments.get("action")
    if not action:
        return [
            types.TextContent(
                type="text", text="Error: 'action' is required."
            )
        ]

    if action in SESSION_ACTIONS:
        return await handle_research_context(arguments)
    elif action in MEMORY_ACTIONS:
        return await handle_research_memory(arguments)
    else:
        valid = ", ".join(ALL_ACTIONS)
        return [
            types.TextContent(
                type="text",
                text=f"Error: Unknown action '{action}'. Valid actions: {valid}.",
            )
        ]
