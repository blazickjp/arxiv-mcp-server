"""
Arxiv MCP Server
===============

This module implements an MCP server for interacting with arXiv.
"""

import logging
import mcp.types as types
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount
from typing import Dict, Any, List
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
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
        elif name == "get_abstract":
            return await handle_get_abstract(arguments)
        elif name == "semantic_search":
            return await handle_semantic_search(arguments)
        elif name == "reindex":
            return await handle_reindex(arguments)
        elif name == "citation_graph":
            return await handle_citation_graph(arguments)
        elif name == "watch_topic":
            return await handle_watch_topic(arguments)
        elif name == "check_alerts":
            return await handle_check_alerts(arguments)
        else:
            return [types.TextContent(type="text", text=f"Error: Unknown tool {name}")]
    except Exception as e:
        logger.error(f"Tool error: {str(e)}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]

async def main():
    """Run the server async context."""
    if settings.TRANSPORT == "http":
        session_manager = StreamableHTTPSessionManager(
            app=server,
            event_store=None,
            json_response=False,
        )
        starlette_app = Starlette(
            routes=[Mount("/mcp", app=session_manager.handle_request)],
        )
        config = uvicorn.Config(
            starlette_app,
            host=settings.HOST,
            port=settings.PORT,
            log_level="info",
        )
        uvicorn_server = uvicorn.Server(config)
        async with session_manager.run():
            await uvicorn_server.serve()
    else:
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
