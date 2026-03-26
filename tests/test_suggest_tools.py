"""Tests for the semantic tool discovery module (suggest_tools)."""

import json
import pytest
import mcp.types as types

import research_mcp_server.tools.suggest_tools as suggest_mod
from research_mcp_server.tools.suggest_tools import (
    ToolIndex,
    handle_suggest_tools,
    register_all_tools,
    suggest_tools_tool,
)


# ---------------------------------------------------------------------------
# Helper: build a realistic set of mock Tool objects
# ---------------------------------------------------------------------------

def _make_mock_tools() -> list[types.Tool]:
    """Return ~10 Tool objects mimicking the real server tool set (v2 names)."""
    return [
        types.Tool(
            name="search",
            description="Unified arXiv search — keyword/phrase queries AND structured field-by-field searches. Returns titles, abstracts, authors, and IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query for arXiv papers"},
                    "title": {"type": "string", "description": "Search in paper titles"},
                    "author": {"type": "string", "description": "Search by author name"},
                    "max_results": {"type": "integer", "description": "Maximum results to return"},
                },
            },
        ),
        types.Tool(
            name="download_paper",
            description="Download an arXiv paper PDF by its ID and store it locally for reading.",
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "description": "The arXiv paper ID to download"},
                },
                "required": ["paper_id"],
            },
        ),
        types.Tool(
            name="export",
            description="Export metadata for known arXiv paper IDs as BibTeX, Markdown, JSON, or CSV for bibliography and references.",
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_ids": {"type": "array", "items": {"type": "string"}, "description": "List of arXiv paper IDs"},
                    "format": {"type": "string", "description": "Output format: bibtex, markdown, json, csv"},
                },
                "required": ["paper_ids"],
            },
        ),
        types.Tool(
            name="semantic_search",
            description="Find semantically similar papers using embedding-based vector search over downloaded paper abstracts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query for semantic similarity"},
                    "top_k": {"type": "integer", "description": "Number of results"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="compare",
            description="Compare two or more arXiv papers side-by-side, highlighting differences in methods, results, and contributions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_ids": {"type": "array", "items": {"type": "string"}, "description": "Paper IDs to compare"},
                },
                "required": ["paper_ids"],
            },
        ),
        types.Tool(
            name="citations",
            description="Get citations and references for a paper via Semantic Scholar. Optionally analyze citation landscape with structural analysis.",
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "description": "arXiv paper ID"},
                    "direction": {"type": "string", "description": "citations or references"},
                    "analyze": {"type": "boolean", "description": "Enable structural analysis"},
                },
                "required": ["paper_id"],
            },
        ),
        types.Tool(
            name="hf_trending",
            description="Get trending machine learning papers from Hugging Face daily papers feed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max papers to return"},
                },
            },
        ),
        types.Tool(
            name="digest",
            description="Generate a research digest summarizing recent papers in a topic area with key findings and trends.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Research topic for the digest"},
                    "days": {"type": "integer", "description": "Look back N days"},
                },
                "required": ["topic"],
            },
        ),
        types.Tool(
            name="trends",
            description="Analyze publication trends over time for a research topic, showing volume changes and emerging subtopics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Research topic to analyze"},
                    "years": {"type": "integer", "description": "Number of years to analyze"},
                },
                "required": ["topic"],
            },
        ),
    ]


@pytest.fixture(autouse=True)
def _register_mock_tools():
    """Register mock tools before each test, clean up after."""
    tools = _make_mock_tools()
    register_all_tools(tools)
    # Force index rebuild (ignore any stale pickle on disk)
    suggest_mod._index = None
    yield
    register_all_tools([])
    suggest_mod._index = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tool_definition():
    """Verify the suggest_tools tool has the correct name and schema shape."""
    assert suggest_tools_tool.name == "help"
    schema = suggest_tools_tool.inputSchema
    assert "query" in schema["properties"]
    assert "top_k" in schema["properties"]
    assert "query" in schema["required"]
    assert schema["properties"]["top_k"]["maximum"] == 10


def test_tool_index_build_text():
    """Verify _build_tool_text concatenates name, description, and param descriptions."""
    tool_dict = {
        "name": "my_tool",
        "description": "Does something useful",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
        },
    }
    text = ToolIndex._build_tool_text(tool_dict)
    assert "my_tool" in text
    assert "Does something useful" in text
    assert "The search query" in text
    assert "Max results" in text


@pytest.mark.asyncio
async def test_suggest_tools_returns_results():
    """Integration: querying for ML papers should surface search tools in top 3."""
    result = await handle_suggest_tools({"query": "find papers about machine learning"})
    assert len(result) == 1
    data = json.loads(result[0].text)

    assert "suggestions" in data
    suggestions = data["suggestions"]
    assert len(suggestions) > 0

    top_names = [s["tool_name"] for s in suggestions[:5]]
    search_tools = {"search", "semantic_search"}
    assert any(
        name in top_names
        for name in search_tools
    ), f"Expected a search-related tool in top 5, got: {top_names}"


@pytest.mark.asyncio
async def test_suggest_tools_export_query():
    """Integration: querying about bibliography export should surface export tool."""
    result = await handle_suggest_tools({"query": "export bibliography references"})
    data = json.loads(result[0].text)
    tool_names = [s["tool_name"] for s in data["suggestions"]]
    assert "export" in tool_names, f"Expected export in results, got: {tool_names}"


@pytest.mark.asyncio
async def test_suggest_tools_token_savings():
    """Verify that reduction_percent > 0 when selecting fewer than all tools."""
    result = await handle_suggest_tools({"query": "search for recent papers", "top_k": 3})
    data = json.loads(result[0].text)

    savings = data["token_savings"]
    assert savings["all_tools_chars"] > 0
    assert savings["selected_tools_chars"] > 0
    assert savings["reduction_percent"] > 0, (
        f"Expected positive reduction, got {savings['reduction_percent']}%"
    )
