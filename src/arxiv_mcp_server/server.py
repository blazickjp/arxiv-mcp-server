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
from .tools import (
    handle_search,
    handle_download,
    handle_list_papers,
    handle_read_paper,
    handle_get_abstract,
)
from .tools import search_tool, download_tool, list_tool, read_tool, abstract_tool
from .tools import (
    handle_semantic_search,
    handle_reindex,
    semantic_search_tool,
    reindex_tool,
    handle_citation_graph,
    citation_graph_tool,
    handle_watch_topic,
    watch_topic_tool,
    handle_check_alerts,
    check_alerts_tool,
)
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
        search_tool,
        download_tool,
        list_tool,
        read_tool,
        abstract_tool,
        semantic_search_tool,
        reindex_tool,
        citation_graph_tool,
        watch_topic_tool,
        check_alerts_tool,
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: Dict[str, Any]
) -> types.CallToolResult:
    """Handle tool calls for arXiv research functionality."""
    logger.debug(f"Calling tool {name} with arguments {arguments}")
    try:
        if name == "search_papers":
            content = await handle_search(arguments)
        elif name == "download_paper":
            content = await handle_download(arguments)
        elif name == "list_papers":
            content = await handle_list_papers(arguments)
        elif name == "read_paper":
            content = await handle_read_paper(arguments)
        elif name == "get_abstract":
            content = await handle_get_abstract(arguments)
        elif name == "semantic_search":
            content = await handle_semantic_search(arguments)
        elif name == "reindex":
            content = await handle_reindex(arguments)
        elif name == "citation_graph":
            content = await handle_citation_graph(arguments)
        elif name == "watch_topic":
            content = await handle_watch_topic(arguments)
        elif name == "check_alerts":
            content = await handle_check_alerts(arguments)
        else:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )
        return types.CallToolResult(
            content=content,
            isError=_is_error_content(content),
        )
    except Exception as e:
        logger.error(f"Tool error: {str(e)}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {str(e)}")],
            isError=True,
        )


def _is_error_content(content: List[types.TextContent]) -> bool:
    """Check if the tool response content indicates an error.

    Tool handlers return {"status": "error", ...} in the text body for error
    cases. This helper detects that pattern so the dispatcher can set isError=True
    on the CallToolResult, letting MCP clients distinguish errors from successes.
    """
    import json

    if not content:
        return False
    for item in content:
        if hasattr(item, "text") and item.text:
            try:
                data = json.loads(item.text)
                if isinstance(data, dict) and data.get("status") == "error":
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
    return False


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
