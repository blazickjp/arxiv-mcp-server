"""Engram-pattern research memory MCP tool.

Wraps ResearchMemory to provide persistent session, thesis, and digest
tracking across Claude sessions. Use `warm_context` at the START of a
research run to load accumulated knowledge; use `save_digest` at the END
to persist findings for next time.
"""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..store.research_memory import ResearchMemory

logger = logging.getLogger("research-mcp-server")

_memory = ResearchMemory()

research_memory_tool = types.Tool(
    name="research_memory",
    description=(
        "Persistent research memory using the Engram pattern. Tracks sessions, "
        "thesis evolution, and session digests across Claude sessions.\n\n"
        "WORKFLOW:\n"
        "1. At START of research: call with action='warm_context' to load accumulated "
        "knowledge and avoid re-discovering known patterns.\n"
        "2. During research: create sessions, track theses, update confidence.\n"
        "3. At END of research: call with action='save_digest' to persist findings "
        "for the next run.\n\n"
        "Actions: create_session, list_sessions, close_session, add_thesis, "
        "update_thesis, list_theses, save_digest, warm_context"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create_session",
                    "list_sessions",
                    "close_session",
                    "add_thesis",
                    "update_thesis",
                    "list_theses",
                    "save_digest",
                    "warm_context",
                ],
                "description": "The action to perform.",
            },
            "name": {
                "type": "string",
                "description": "Session name. Required for create_session.",
            },
            "goal": {
                "type": "string",
                "description": "Research goal. Optional for create_session.",
            },
            "status": {
                "type": "string",
                "description": "Filter by status. Optional for list_sessions.",
            },
            "session_id": {
                "type": "string",
                "description": "Session UUID. Required for close_session. Optional for save_digest.",
            },
            "statement": {
                "type": "string",
                "description": "Thesis statement. Required for add_thesis.",
            },
            "category": {
                "type": "string",
                "enum": ["primary", "secondary", "exploratory"],
                "description": "Thesis category. Optional for add_thesis (default: exploratory).",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence score 0-1. Optional for add_thesis and update_thesis.",
            },
            "thesis_id": {
                "type": "string",
                "description": "Thesis UUID. Required for update_thesis.",
            },
            "thesis_status": {
                "type": "string",
                "enum": ["active", "validated", "invalidated", "dormant"],
                "description": "Thesis status. Optional for update_thesis.",
            },
            "evidence": {
                "type": "object",
                "description": "Evidence object to append. Optional for update_thesis.",
            },
            "content": {
                "type": "string",
                "description": "Digest content text. Required for save_digest.",
            },
            "validated_theses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of validated thesis descriptions. Optional for save_digest.",
            },
            "emerging_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of emerging pattern descriptions. Optional for save_digest.",
            },
        },
        "required": ["action"],
    },
)


async def handle_research_memory(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Dispatch research memory actions."""
    action = arguments.get("action")

    try:
        if action == "create_session":
            name = arguments.get("name")
            if not name:
                return _error("'name' is required for create_session.")
            goal = arguments.get("goal", "")
            session_id = await _memory.create_session(name=name, goal=goal)
            return _ok({"session_id": session_id, "status": "created"})

        elif action == "list_sessions":
            status = arguments.get("status")
            sessions = await _memory.list_sessions(status=status)
            return _ok({"sessions": sessions, "count": len(sessions)})

        elif action == "close_session":
            session_id = arguments.get("session_id")
            if not session_id:
                return _error("'session_id' is required for close_session.")
            await _memory.close_session(session_id=session_id)
            return _ok({"session_id": session_id, "status": "closed"})

        elif action == "add_thesis":
            statement = arguments.get("statement")
            if not statement:
                return _error("'statement' is required for add_thesis.")
            category = arguments.get("category", "exploratory")
            confidence = arguments.get("confidence", 0.5)
            thesis_id = await _memory.add_thesis(
                statement=statement,
                category=category,
                confidence=confidence,
            )
            return _ok({"thesis_id": thesis_id, "status": "created"})

        elif action == "update_thesis":
            thesis_id = arguments.get("thesis_id")
            if not thesis_id:
                return _error("'thesis_id' is required for update_thesis.")
            confidence = arguments.get("confidence")
            status = arguments.get("thesis_status")
            evidence = arguments.get("evidence")
            evidence_str = json.dumps(evidence) if evidence is not None else None
            await _memory.update_thesis(
                thesis_id=thesis_id,
                confidence=confidence,
                status=status,
                evidence=evidence_str,
            )
            return _ok({"thesis_id": thesis_id, "status": "updated"})

        elif action == "list_theses":
            theses = await _memory.get_active_theses()
            return _ok({"theses": theses, "count": len(theses)})

        elif action == "save_digest":
            content = arguments.get("content")
            if not content:
                return _error("'content' is required for save_digest.")
            digest_id = await _memory.save_digest(
                content=content,
                session_id=arguments.get("session_id"),
                validated_theses=arguments.get("validated_theses"),
                emerging_patterns=arguments.get("emerging_patterns"),
            )
            return _ok({"digest_id": digest_id, "status": "saved"})

        elif action == "warm_context":
            context = await _memory.get_warm_context()
            return _ok(context)

        else:
            return _error(f"Unknown action: {action}")

    except Exception as e:
        logger.exception("research_memory error for action=%s", action)
        return _error(f"Error in {action}: {e}")


def _ok(data: Any) -> List[types.TextContent]:
    """Return a success JSON response."""
    return [types.TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def _error(message: str) -> List[types.TextContent]:
    """Return an error JSON response."""
    return [types.TextContent(type="text", text=json.dumps({"error": message}, indent=2))]
