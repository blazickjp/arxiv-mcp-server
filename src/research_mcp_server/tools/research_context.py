"""Track research session state across MCP calls.

Maintains persistent context for research sessions: which papers have been
examined, open questions to follow, and key findings with evidence links.
Enables continuity across multiple tool invocations.
"""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..store.research_context import ResearchContext

logger = logging.getLogger("research-mcp-server")

research_context_tool = types.Tool(
    name="research_context",
    description="""Track and manage research session state across multiple interactions.

Maintains persistent context for a research session: papers examined, open
questions/threads, and key findings with evidence. Use this to keep track of
where you are in a multi-step research process.

Actions:
- create: Start a new research session with a name and optional goal.
- status: Get current session state (paper count, open questions, findings).
- log_paper: Record that a paper was searched/read/saved/compared/cited.
- add_question: Add an open research question or thread to follow.
- answer_question: Mark a question as answered with the answer text.
- add_finding: Record a key finding with optional evidence paper IDs.
- summarize: Get full session summary with all papers, threads, and findings.
- list: List all research sessions (optionally filtered by status).
- complete: Mark a session as completed.""",
    inputSchema={
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "description": "The operation to perform.",
                "enum": [
                    "create",
                    "status",
                    "log_paper",
                    "add_question",
                    "answer_question",
                    "add_finding",
                    "summarize",
                    "list",
                    "complete",
                ],
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Session identifier. Required for most actions except "
                    "'create' and 'list'. If omitted, uses the most recent "
                    "active session."
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
        },
    },
)


async def _resolve_session_id(
    ctx: ResearchContext, arguments: Dict[str, Any]
) -> str:
    """Resolve session_id from arguments or fall back to active session.

    Args:
        ctx: ResearchContext instance.
        arguments: Tool arguments dict.

    Returns:
        A valid session ID string.

    Raises:
        ValueError: If no session_id provided and no active session exists.
    """
    session_id = arguments.get("session_id")
    if session_id:
        return session_id

    active = await ctx.get_active_session()
    if active is None:
        raise ValueError(
            "No session_id provided and no active session found. "
            "Create a session first with action='create'."
        )
    return active["id"]


async def handle_research_context(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle research context tool calls.

    Routes to the appropriate ResearchContext method based on the action
    parameter.

    Args:
        arguments: Tool input with action and action-specific parameters.

    Returns:
        List containing a single TextContent with JSON results.
    """
    try:
        action = arguments.get("action")
        if not action:
            return [
                types.TextContent(
                    type="text", text="Error: 'action' is required."
                )
            ]

        ctx = ResearchContext()

        if action == "create":
            name = arguments.get("name")
            if not name:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: 'name' is required for 'create' action.",
                    )
                ]
            result = await ctx.create_session(
                name=name, goal=arguments.get("goal")
            )
            logger.info(f"Research session created: {result['id']} ({name})")
            return [
                types.TextContent(
                    type="text", text=json.dumps(result, indent=2)
                )
            ]

        if action == "list":
            sessions = await ctx.list_sessions(
                status=arguments.get("status")
            )
            response = {"total": len(sessions), "sessions": sessions}
            return [
                types.TextContent(
                    type="text", text=json.dumps(response, indent=2)
                )
            ]

        if action == "status":
            session_id = await _resolve_session_id(ctx, arguments)
            session = await ctx.get_session(session_id)
            if session is None:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: Session '{session_id}' not found.",
                    )
                ]
            return [
                types.TextContent(
                    type="text", text=json.dumps(session, indent=2)
                )
            ]

        if action == "log_paper":
            session_id = await _resolve_session_id(ctx, arguments)
            paper_id = arguments.get("paper_id")
            paper_action = arguments.get("paper_action")
            if not paper_id or not paper_action:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: 'paper_id' and 'paper_action' are required for 'log_paper'.",
                    )
                ]
            result = await ctx.log_paper(
                session_id=session_id,
                paper_id=paper_id,
                action=paper_action,
                notes=arguments.get("notes"),
            )
            logger.info(
                f"Paper {paper_id} logged as '{paper_action}' "
                f"in session {session_id}"
            )
            return [
                types.TextContent(
                    type="text", text=json.dumps(result, indent=2)
                )
            ]

        if action == "add_question":
            session_id = await _resolve_session_id(ctx, arguments)
            question = arguments.get("question")
            if not question:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: 'question' is required for 'add_question'.",
                    )
                ]
            result = await ctx.add_thread(
                session_id=session_id, question=question
            )
            logger.info(f"Thread added to session {session_id}: {question[:60]}")
            return [
                types.TextContent(
                    type="text", text=json.dumps(result, indent=2)
                )
            ]

        if action == "answer_question":
            thread_id = arguments.get("thread_id")
            answer = arguments.get("answer")
            if thread_id is None or not answer:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: 'thread_id' and 'answer' are required for 'answer_question'.",
                    )
                ]
            result = await ctx.update_thread(
                thread_id=int(thread_id),
                status="answered",
                answer=answer,
            )
            if result is None:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: Thread {thread_id} not found.",
                    )
                ]
            logger.info(f"Thread {thread_id} answered")
            return [
                types.TextContent(
                    type="text", text=json.dumps(result, indent=2)
                )
            ]

        if action == "add_finding":
            session_id = await _resolve_session_id(ctx, arguments)
            finding = arguments.get("finding")
            if not finding:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: 'finding' is required for 'add_finding'.",
                    )
                ]
            result = await ctx.add_finding(
                session_id=session_id,
                finding=finding,
                evidence_paper_ids=arguments.get("evidence_paper_ids"),
            )
            logger.info(f"Finding added to session {session_id}")
            return [
                types.TextContent(
                    type="text", text=json.dumps(result, indent=2)
                )
            ]

        if action == "summarize":
            session_id = await _resolve_session_id(ctx, arguments)
            summary = await ctx.get_session_summary(session_id)
            if summary is None:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: Session '{session_id}' not found.",
                    )
                ]
            logger.info(f"Session summary generated for {session_id}")
            return [
                types.TextContent(
                    type="text", text=json.dumps(summary, indent=2)
                )
            ]

        if action == "complete":
            session_id = await _resolve_session_id(ctx, arguments)
            result = await ctx.update_session(
                session_id, status="completed"
            )
            if result is None:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: Session '{session_id}' not found.",
                    )
                ]
            logger.info(f"Session {session_id} marked as completed")
            return [
                types.TextContent(
                    type="text", text=json.dumps(result, indent=2)
                )
            ]

        return [
            types.TextContent(
                type="text",
                text=f"Error: Unknown action '{action}'. "
                f"Valid actions: create, status, log_paper, add_question, "
                f"answer_question, add_finding, summarize, list, complete.",
            )
        ]

    except ValueError as e:
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    except Exception as e:
        logger.error(f"Research context error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
