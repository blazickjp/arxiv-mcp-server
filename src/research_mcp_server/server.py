"""
Research MCP Server
===================

Multi-source research intelligence server. Provides unified tools for
academic paper search, citation analysis, knowledge management, and
(planned) practitioner community intelligence.
"""

import logging
import time
import mcp.types as types
from typing import Dict, Any, List
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions
from mcp.server.stdio import stdio_server
from .config import Settings

# --- Consolidated v2 tools ---
from .tools import search_tool, handle_search
from .tools import kb_tool, handle_kb
from .tools import memory_tool, handle_memory
from .tools import citations_tool, handle_citations

# --- Renamed tools (dropped arxiv_ prefix) ---
from .tools import semantic_search_tool, handle_semantic_search
from .tools import compare_tool, handle_compare
from .tools import trend_analysis_tool, handle_trend_analysis
from .tools import digest_tool, handle_digest
from .tools import research_lineage_tool, handle_research_lineage
from .tools import export_tool, handle_export
from .tools import multi_search_tool, handle_multi_search
from .tools import pwc_search_tool, handle_pwc_search
from .tools import suggest_tools_tool, handle_suggest_tools

# --- Unchanged tools ---
from .tools import download_tool, handle_download
from .tools import list_tool, handle_list_papers
from .tools import read_tool, handle_read_paper
from .tools import read_paper_chunks_tool, handle_read_paper_chunks
from .tools import kg_query_tool, handle_kg_query
from .tools import hf_trending_tool, handle_hf_trending
from .tools import model_benchmarks_tool, handle_model_benchmarks
from .tools import venue_lookup_tool, handle_venue_lookup
from .tools import patent_search_tool, handle_patent_search

# --- Phase 1: Practitioner sources (no auth) ---
from .tools import hn_tool, handle_hn
from .tools import community_tool, handle_community
from .tools import packages_tool, handle_packages

# --- Phase 2: Auth sources ---
from .tools import github_tool, handle_github
from .tools import reddit_tool, handle_reddit

# --- Phase 3: Composite CTO intelligence ---
from .tools import tech_pulse_tool, handle_tech_pulse
from .tools import evaluate_tool, handle_evaluate
from .tools import sentiment_tool, handle_sentiment
from .tools import deep_research_tool, handle_deep_research

# --- Backwards-compat handlers (old tool names still routable) ---
from .tools import handle_advanced_query
from .tools import handle_citation_graph, handle_citation_context
from .tools import handle_kb_save, handle_kb_search, handle_kb_list
from .tools import handle_kb_annotate, handle_kb_remove
from .tools import handle_research_context, handle_research_memory

from .tools.suggest_tools import register_all_tools
from .prompts.handlers import list_prompts as handler_list_prompts
from .prompts.handlers import get_prompt as handler_get_prompt
from .security import sanitize_tool_response, check_response_size
from .store.research_history import ResearchHistory

_history = ResearchHistory()

settings = Settings()
logger = logging.getLogger("research-mcp-server")
logger.setLevel(logging.INFO)
server = Server(settings.APP_NAME)


@server.list_prompts()
async def list_prompts() -> List[types.Prompt]:
    """List available prompts."""
    return await handler_list_prompts()


@server.get_prompt()
async def get_prompt(
    name: str, arguments: Dict[str, str] | None = None
) -> types.GetPromptResult:
    """Get a specific prompt with arguments."""
    return await handler_get_prompt(name, arguments)


@server.list_tools()
async def list_tools() -> List[types.Tool]:
    """List available research tools.

    Returns v2 tools (31 tools across academic, practitioner, and intelligence layers).
    Old tool names are still routable via backwards-compat aliases in _TOOL_HANDLERS.
    """
    return [
        # Search & Discovery
        search_tool, semantic_search_tool, multi_search_tool,
        # Paper Management
        download_tool, list_tool, read_tool, read_paper_chunks_tool,
        # Analysis
        citations_tool, research_lineage_tool, compare_tool,
        trend_analysis_tool, digest_tool,
        # Knowledge & Memory
        kb_tool, kg_query_tool, memory_tool,
        # Academic Sources
        hf_trending_tool, pwc_search_tool, model_benchmarks_tool,
        venue_lookup_tool, patent_search_tool, export_tool,
        # Practitioner Sources
        hn_tool, community_tool, packages_tool,
        github_tool, reddit_tool,
        # CTO Intelligence
        tech_pulse_tool, evaluate_tool, sentiment_tool, deep_research_tool,
        # Meta
        suggest_tools_tool,
    ]


# --- Primary handlers (v2 tool names) ---
_TOOL_HANDLERS: Dict[str, Any] = {
    # Search & Discovery
    "search": handle_search,
    "semantic_search": handle_semantic_search,
    "cross_search": handle_multi_search,
    # Paper Management
    "download_paper": handle_download,
    "list_papers": handle_list_papers,
    "read_paper": handle_read_paper,
    "read_paper_chunks": handle_read_paper_chunks,
    # Analysis
    "citations": handle_citations,
    "lineage": handle_research_lineage,
    "compare": handle_compare,
    "trends": handle_trend_analysis,
    "digest": handle_digest,
    # Knowledge & Memory
    "kb": handle_kb,
    "kg_query": handle_kg_query,
    "memory": handle_memory,
    # Academic Sources
    "hf_trending": handle_hf_trending,
    "benchmarks": handle_pwc_search,
    "model_benchmarks": handle_model_benchmarks,
    "venue_lookup": handle_venue_lookup,
    "patent_search": handle_patent_search,
    "export": handle_export,
    # Practitioner Sources
    "hn": handle_hn,
    "community": handle_community,
    "packages": handle_packages,
    "github": handle_github,
    "reddit": handle_reddit,
    # CTO Intelligence
    "tech_pulse": handle_tech_pulse,
    "evaluate": handle_evaluate,
    "sentiment": handle_sentiment,
    "deep_research": handle_deep_research,
    # Meta
    "help": handle_suggest_tools,
}

# --- Backwards-compat aliases (old tool names → same handlers) ---
_COMPAT_ALIASES: Dict[str, str] = {
    "search_papers": "search",
    "arxiv_advanced_query": "search",
    "arxiv_semantic_search": "semantic_search",
    "arxiv_compare_papers": "compare",
    "arxiv_citation_graph": "citations",
    "arxiv_citation_context": "citations",
    "arxiv_trend_analysis": "trends",
    "arxiv_research_digest": "digest",
    "arxiv_research_lineage": "lineage",
    "arxiv_export": "export",
    "multi_search": "cross_search",
    "papers_with_code_search": "benchmarks",
    "hf_trending_papers": "hf_trending",
    "suggest_tools": "help",
    "kb_save": "kb",
    "kb_search": "kb",
    "kb_list": "kb",
    "kb_annotate": "kb",
    "kb_remove": "kb",
    "research_context": "memory",
    "research_memory": "memory",
}

# Register backwards-compat aliases — old names route to the same handlers
# For kb_* aliases, we need to inject the action param
_KB_ACTION_MAP = {
    "kb_save": "save",
    "kb_search": "search",
    "kb_list": "list",
    "kb_annotate": "annotate",
    "kb_remove": "remove",
}

for _old_name, _new_name in _COMPAT_ALIASES.items():
    if _old_name not in _KB_ACTION_MAP and _old_name not in ("research_context", "research_memory"):
        _TOOL_HANDLERS[_old_name] = _TOOL_HANDLERS[_new_name]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tool calls for research functionality.

    Every call is auto-logged to research_history.db for audit trail.
    Supports backwards-compat aliases for old tool names.
    """
    logger.debug(f"Calling tool {name} with arguments {arguments}")
    start = time.monotonic()
    is_error = False

    try:
        handler = _TOOL_HANDLERS.get(name)

        # Handle backwards-compat for kb_* aliases (inject action param)
        if handler is None and name in _KB_ACTION_MAP:
            arguments = {**arguments, "action": _KB_ACTION_MAP[name]}
            handler = _TOOL_HANDLERS["kb"]

        # Handle backwards-compat for research_context/research_memory
        if handler is None and name == "research_context":
            handler = handle_research_context
        elif handler is None and name == "research_memory":
            handler = handle_research_memory

        if handler is None:
            is_error = True
            result = [types.TextContent(type="text", text=f"Error: Unknown tool {name}")]
        else:
            result = await handler(arguments)
    except Exception as e:
        logger.error(f"Tool error: {str(e)}")
        is_error = True
        result = [types.TextContent(type="text", text=f"Error: {str(e)}")]

    # Sanitize tool responses
    for i, content in enumerate(result):
        if hasattr(content, "text"):
            sanitized = sanitize_tool_response(content.text)
            if sanitized != content.text:
                logger.warning(f"Tool '{name}': response sanitized (injection pattern removed)")
                result[i] = types.TextContent(type="text", text=sanitized)
            if not check_response_size(sanitized):
                logger.warning(f"Tool '{name}': response truncated (exceeded size limit)")
                result[i] = types.TextContent(
                    type="text",
                    text=sanitized[:500_000] + "\n\n[Response truncated — exceeded 500KB limit]",
                )

    # Auto-log to research history
    duration_ms = int((time.monotonic() - start) * 1000)
    response_text = "\n".join(r.text for r in result)
    try:
        await _history.log_call(
            tool_name=name,
            arguments=arguments,
            response_text=response_text,
            is_error=is_error,
            duration_ms=duration_ms,
        )
    except Exception as log_err:
        logger.warning(f"Failed to log tool call: {log_err}")

    return result


async def main():
    """Run the server async context."""
    # Register all tools for semantic discovery
    all_tools = await list_tools()
    register_all_tools(all_tools)

    async with stdio_server() as streams:
        await server.run(
            streams[0],
            streams[1],
            InitializationOptions(
                server_name=settings.APP_NAME,
                server_version=settings.APP_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(resources_changed=True),
                    experimental_capabilities={},
                ),
            ),
        )
