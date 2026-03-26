"""Tool definitions for the arXiv MCP server."""

from .search import search_tool, handle_search
from .download import download_tool, handle_download
from .list_papers import list_tool, handle_list_papers
from .read_paper import read_tool, handle_read_paper
from .advanced_query import advanced_query_tool, handle_advanced_query
from .export import export_tool, handle_export
from .semantic_search import semantic_search_tool, handle_semantic_search
from .compare import compare_tool, handle_compare
from .citations import citation_graph_tool, handle_citation_graph
from .citation_context import citation_context_tool, handle_citation_context
from .trends import trend_analysis_tool, handle_trend_analysis
from .digest import digest_tool, handle_digest
from .kb_save import kb_save_tool, handle_kb_save
from .kb_search import kb_search_tool, handle_kb_search
from .kb_list import kb_list_tool, handle_kb_list
from .kb_annotate import kb_annotate_tool, handle_kb_annotate
from .kb_remove import kb_remove_tool, handle_kb_remove

__all__ = [
    "search_tool",
    "download_tool",
    "read_tool",
    "handle_search",
    "handle_download",
    "handle_read_paper",
    "list_tool",
    "handle_list_papers",
    "advanced_query_tool",
    "handle_advanced_query",
    "export_tool",
    "handle_export",
    "semantic_search_tool",
    "handle_semantic_search",
    "compare_tool",
    "handle_compare",
    "citation_graph_tool",
    "handle_citation_graph",
    "trend_analysis_tool",
    "handle_trend_analysis",
    "digest_tool",
    "handle_digest",
    "kb_save_tool",
    "handle_kb_save",
    "kb_search_tool",
    "handle_kb_search",
    "kb_list_tool",
    "handle_kb_list",
    "kb_annotate_tool",
    "handle_kb_annotate",
    "kb_remove_tool",
    "handle_kb_remove",
    "citation_context_tool",
    "handle_citation_context",
]
