"""
Arxiv MCP Server
===============

This module implements an MCP server for interacting with arXiv.
"""

import logging
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
from .prompts.handlers import list_prompts as handler_list_prompts
from .prompts.handlers import get_prompt as handler_get_prompt

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
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tool calls for arXiv research functionality."""
    logger.debug(f"Calling tool {name} with arguments {arguments}")
    try:
        if name == "search_papers":
            return await handle_search(arguments)
        elif name == "download_paper":
            return await handle_download(arguments)
        elif name == "list_papers":
            return await handle_list_papers(arguments)
        elif name == "read_paper":
            return await handle_read_paper(arguments)
        elif name == "arxiv_advanced_query":
            return await handle_advanced_query(arguments)
        elif name == "arxiv_export":
            return await handle_export(arguments)
        elif name == "arxiv_semantic_search":
            return await handle_semantic_search(arguments)
        elif name == "arxiv_compare_papers":
            return await handle_compare(arguments)
        elif name == "arxiv_citation_graph":
            return await handle_citation_graph(arguments)
        elif name == "arxiv_citation_context":
            return await handle_citation_context(arguments)
        elif name == "arxiv_trend_analysis":
            return await handle_trend_analysis(arguments)
        elif name == "arxiv_research_digest":
            return await handle_digest(arguments)
        elif name == "kb_save":
            return await handle_kb_save(arguments)
        elif name == "kb_search":
            return await handle_kb_search(arguments)
        elif name == "kb_list":
            return await handle_kb_list(arguments)
        elif name == "kb_annotate":
            return await handle_kb_annotate(arguments)
        elif name == "kb_remove":
            return await handle_kb_remove(arguments)
        elif name == "arxiv_research_lineage":
            return await handle_research_lineage(arguments)
        elif name == "read_paper_chunks":
            return await handle_read_paper_chunks(arguments)
        elif name == "kg_query":
            return await handle_kg_query(arguments)
        elif name == "research_context":
            return await handle_research_context(arguments)
        else:
            return [types.TextContent(type="text", text=f"Error: Unknown tool {name}")]
    except Exception as e:
        logger.error(f"Tool error: {str(e)}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]


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
