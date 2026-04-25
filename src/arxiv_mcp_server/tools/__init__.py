"""Tool definitions for the arXiv MCP server."""

from .search import search_tool, handle_search
from .download import download_tool, handle_download
from .list_papers import list_tool, handle_list_papers
from .read_paper import read_tool, handle_read_paper
from .get_abstract import abstract_tool, handle_get_abstract
from .semantic_search import (
    semantic_search_tool,
    handle_semantic_search,
    reindex_tool,
    handle_reindex,
)
from .citation_graph import citation_graph_tool, handle_citation_graph
from .alerts import (
    watch_topic_tool,
    handle_watch_topic,
    check_alerts_tool,
    handle_check_alerts,
)
from .latex import (
    get_paper_latex_tool,
    handle_get_paper_latex,
    get_paper_latex_abstract_tool,
    handle_get_paper_latex_abstract,
    list_paper_latex_sections_tool,
    handle_list_paper_latex_sections,
    get_paper_latex_section_tool,
    handle_get_paper_latex_section,
)

__all__ = [
    "search_tool",
    "download_tool",
    "list_tool",
    "read_tool",
    "abstract_tool",
    "handle_search",
    "handle_download",
    "handle_list_papers",
    "handle_read_paper",
    "handle_get_abstract",
    "semantic_search_tool",
    "handle_semantic_search",
    "reindex_tool",
    "handle_reindex",
    "citation_graph_tool",
    "handle_citation_graph",
    "watch_topic_tool",
    "handle_watch_topic",
    "check_alerts_tool",
    "handle_check_alerts",
    "get_paper_latex_tool",
    "handle_get_paper_latex",
    "get_paper_latex_abstract_tool",
    "handle_get_paper_latex_abstract",
    "list_paper_latex_sections_tool",
    "handle_list_paper_latex_sections",
    "get_paper_latex_section_tool",
    "handle_get_paper_latex_section",
]
