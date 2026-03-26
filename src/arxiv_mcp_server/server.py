"""
Arxiv MCP Server
===============

This module implements an MCP server for interacting with arXiv.
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
from .tools import handle_search, handle_download, handle_list_papers, handle_read_paper
from .tools import handle_advanced_query, handle_export
from .tools import handle_semantic_search, handle_compare
from .tools import handle_citation_graph, handle_citation_context, handle_trend_analysis, handle_digest
from .tools import search_tool, download_tool, list_tool, read_tool
from .tools import advanced_query_tool, export_tool
from .tools import semantic_search_tool, compare_tool
from .tools import citation_graph_tool, citation_context_tool, trend_analysis_tool, digest_tool
from .tools import kb_save_tool, kb_search_tool, kb_list_tool, kb_annotate_tool, kb_remove_tool
from .tools import handle_kb_save, handle_kb_search, handle_kb_list, handle_kb_annotate, handle_kb_remove
from .tools import research_lineage_tool, read_paper_chunks_tool, kg_query_tool, research_context_tool
from .tools import handle_research_lineage, handle_read_paper_chunks, handle_kg_query, handle_research_context
from .tools import multi_search_tool, pwc_search_tool
from .tools import handle_multi_search, handle_pwc_search
from .prompts.handlers import list_prompts as handler_list_prompts
from .prompts.handlers import get_prompt as handler_get_prompt
from .store.research_history import ResearchHistory

_history = ResearchHistory()

settings = Settings()
logger = logging.getLogger("arxiv-mcp-server")
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
    """List available arXiv research tools."""
    return [
        search_tool, download_tool, list_tool, read_tool,
        advanced_query_tool, export_tool,
        semantic_search_tool, compare_tool,
        citation_graph_tool, citation_context_tool, trend_analysis_tool, digest_tool,
        kb_save_tool, kb_search_tool, kb_list_tool, kb_annotate_tool, kb_remove_tool,
        research_lineage_tool, read_paper_chunks_tool, kg_query_tool, research_context_tool,
        multi_search_tool, pwc_search_tool,
    ]


_TOOL_HANDLERS: Dict[str, Any] = {
    "search_papers": handle_search,
    "download_paper": handle_download,
    "list_papers": handle_list_papers,
    "read_paper": handle_read_paper,
    "arxiv_advanced_query": handle_advanced_query,
    "arxiv_export": handle_export,
    "arxiv_semantic_search": handle_semantic_search,
    "arxiv_compare_papers": handle_compare,
    "arxiv_citation_graph": handle_citation_graph,
    "arxiv_citation_context": handle_citation_context,
    "arxiv_trend_analysis": handle_trend_analysis,
    "arxiv_research_digest": handle_digest,
    "kb_save": handle_kb_save,
    "kb_search": handle_kb_search,
    "kb_list": handle_kb_list,
    "kb_annotate": handle_kb_annotate,
    "kb_remove": handle_kb_remove,
    "arxiv_research_lineage": handle_research_lineage,
    "read_paper_chunks": handle_read_paper_chunks,
    "kg_query": handle_kg_query,
    "research_context": handle_research_context,
    "multi_search": handle_multi_search,
    "papers_with_code_search": handle_pwc_search,
}


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tool calls for arXiv research functionality.

    Every call is auto-logged to research_history.db for audit trail.
    """
    logger.debug(f"Calling tool {name} with arguments {arguments}")
    start = time.monotonic()
    is_error = False

    try:
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            is_error = True
            result = [types.TextContent(type="text", text=f"Error: Unknown tool {name}")]
        else:
            result = await handler(arguments)
    except Exception as e:
        logger.error(f"Tool error: {str(e)}")
        is_error = True
        result = [types.TextContent(type="text", text=f"Error: {str(e)}")]

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
