"""Tool definitions for the research MCP server."""

# --- Consolidated tools (v2) ---
from .search import search_tool, handle_search
from .kb import kb_tool, handle_kb
from .memory import memory_tool, handle_memory
from .citations import citations_tool, handle_citations

# --- Renamed tools (dropped arxiv_ prefix) ---
from .semantic_search import semantic_search_tool, handle_semantic_search
from .compare import compare_tool, handle_compare
from .trends import trend_analysis_tool, handle_trend_analysis
from .digest import digest_tool, handle_digest
from .research_lineage import research_lineage_tool, handle_research_lineage
from .export import export_tool, handle_export
from .multi_search import multi_search_tool, handle_multi_search
from .paper_with_code import pwc_search_tool, handle_pwc_search
from .suggest_tools import suggest_tools_tool, handle_suggest_tools

# --- Unchanged tools ---
from .download import download_tool, handle_download
from .list_papers import list_tool, handle_list_papers
from .read_paper import read_tool, handle_read_paper
from .read_paper_chunks import read_paper_chunks_tool, handle_read_paper_chunks
from .kg_query import kg_query_tool, handle_kg_query
from .hf_papers import hf_trending_tool, handle_hf_trending
from .model_benchmarks import model_benchmarks_tool, handle_model_benchmarks
from .venue_lookup import venue_lookup_tool, handle_venue_lookup
from .patent_search import patent_search_tool, handle_patent_search

# --- Phase 1: Practitioner sources (no auth) ---
from .hn_tools import hn_tool, handle_hn
from .community_tools import community_tool, handle_community
from .package_tools import packages_tool, handle_packages

# --- Backwards-compat imports (old tools still importable) ---
from .advanced_query import advanced_query_tool, handle_advanced_query
from .citations import citation_graph_tool, handle_citation_graph
from .citation_context import citation_context_tool, handle_citation_context
from .kb_save import kb_save_tool, handle_kb_save
from .kb_search import kb_search_tool, handle_kb_search
from .kb_list import kb_list_tool, handle_kb_list
from .kb_annotate import kb_annotate_tool, handle_kb_annotate
from .kb_remove import kb_remove_tool, handle_kb_remove
from .research_context import research_context_tool, handle_research_context
from .research_memory_tools import research_memory_tool, handle_research_memory

__all__ = [
    # Consolidated v2 tools
    "search_tool", "handle_search",
    "kb_tool", "handle_kb",
    "memory_tool", "handle_memory",
    "citations_tool", "handle_citations",
    # Renamed tools
    "semantic_search_tool", "handle_semantic_search",
    "compare_tool", "handle_compare",
    "trend_analysis_tool", "handle_trend_analysis",
    "digest_tool", "handle_digest",
    "research_lineage_tool", "handle_research_lineage",
    "export_tool", "handle_export",
    "multi_search_tool", "handle_multi_search",
    "pwc_search_tool", "handle_pwc_search",
    "suggest_tools_tool", "handle_suggest_tools",
    # Unchanged tools
    "download_tool", "handle_download",
    "list_tool", "handle_list_papers",
    "read_tool", "handle_read_paper",
    "read_paper_chunks_tool", "handle_read_paper_chunks",
    "kg_query_tool", "handle_kg_query",
    "hf_trending_tool", "handle_hf_trending",
    "model_benchmarks_tool", "handle_model_benchmarks",
    "venue_lookup_tool", "handle_venue_lookup",
    "patent_search_tool", "handle_patent_search",
    # Phase 1: Practitioner sources
    "hn_tool", "handle_hn",
    "community_tool", "handle_community",
    "packages_tool", "handle_packages",
    # Backwards-compat
    "advanced_query_tool", "handle_advanced_query",
    "citation_graph_tool", "handle_citation_graph",
    "citation_context_tool", "handle_citation_context",
    "kb_save_tool", "handle_kb_save",
    "kb_search_tool", "handle_kb_search",
    "kb_list_tool", "handle_kb_list",
    "kb_annotate_tool", "handle_kb_annotate",
    "kb_remove_tool", "handle_kb_remove",
    "research_context_tool", "handle_research_context",
    "research_memory_tool", "handle_research_memory",
]
