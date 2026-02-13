"""Tool definitions for the arXiv MCP server."""

from .search import search_tool, handle_search
from .download import download_tool, handle_download
from .list_papers import list_tool, handle_list_papers
from .read_paper import read_tool, handle_read_paper
from .semantic_search import (
    semantic_search_tool,
    handle_semantic_search,
    reindex_tool,
    handle_reindex,
)
from .citation_graph import citation_graph_tool, handle_citation_graph

__all__ = [
    "search_tool",
    "download_tool",
    "read_tool",
    "handle_search",
    "handle_download",
    "handle_read_paper",
    "list_tool",
    "handle_list_papers",
    "semantic_search_tool",
    "handle_semantic_search",
    "reindex_tool",
    "handle_reindex",
    "citation_graph_tool",
    "handle_citation_graph",
]
