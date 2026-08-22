"""Tool schema compatibility and size tests."""

import json

from arxiv_mcp_server.server import list_tools
from arxiv_mcp_server.tools.search import search_tool

# Baseline measured on main @94a1317 (search_papers Tool.description only).
_SEARCH_PAPERS_DESC_BASELINE_CHARS = 3241
_SEARCH_PAPERS_DESC_REDUCTION_TARGET = 0.40

EXPECTED_TOOL_NAMES = {
    "search_papers",
    "download_paper",
    "list_papers",
    "read_paper",
    "get_abstract",
    "semantic_search",
    "reindex",
    "citation_graph",
    "export_citations",
    "watch_topic",
    "check_alerts",
    "list_watches",
    "unwatch_topic",
    "get_paper_latex",
    "list_paper_latex_sections",
    "get_paper_latex_section",
    "get_paper_outline",
    "read_paper_section",
    "search_paper_text",
}


async def test_tool_input_schemas_are_closed():
    """MCP clients expect tool schemas to reject unknown arguments."""
    tools = await list_tools()

    assert tools
    for tool in tools:
        assert tool.inputSchema["type"] == "object"
        assert (
            tool.inputSchema.get("additionalProperties") is False
        ), f"{tool.name} inputSchema must set additionalProperties=False"


async def test_list_tools_metadata_is_valid_mcp():
    """Every tool exposes a name, description, and typed argument schema."""
    tools = await list_tools()
    names = {tool.name for tool in tools}
    assert names == EXPECTED_TOOL_NAMES

    for tool in tools:
        assert isinstance(tool.name, str) and tool.name
        assert isinstance(tool.description, str) and tool.description.strip()
        schema = tool.inputSchema
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        for prop_name, prop_schema in properties.items():
            assert isinstance(prop_name, str) and prop_name
            assert isinstance(prop_schema, dict)
            assert "description" in prop_schema
            assert (
                "type" in prop_schema
                or "anyOf" in prop_schema
                or "oneOf" in prop_schema
                or "$ref" in prop_schema
            )
        required = schema.get("required", [])
        assert isinstance(required, list)
        for req in required:
            assert req in properties


def test_search_papers_schema_semantics_preserved():
    """Required params, enums, and invocation-critical guidance stay intact."""
    schema = search_tool.inputSchema
    props = schema["properties"]

    assert schema["required"] == ["query"]
    assert set(props) == {
        "query",
        "max_results",
        "start",
        "abstract_mode",
        "date_from",
        "date_to",
        "categories",
        "sort_by",
    }
    assert props["query"]["type"] == "string"
    assert props["max_results"]["type"] == "integer"
    assert props["start"]["type"] == "integer"
    assert props["start"]["minimum"] == 0
    assert props["abstract_mode"]["type"] == "string"
    assert props["abstract_mode"]["enum"] == ["none", "snippet", "full"]
    assert props["date_from"]["type"] == "string"
    assert props["date_to"]["type"] == "string"
    assert props["categories"]["type"] == "array"
    assert props["categories"]["items"] == {"type": "string"}
    assert props["sort_by"]["type"] == "string"
    assert props["sort_by"]["enum"] == ["relevance", "date"]

    desc = search_tool.description
    assert "ti:" in desc and "au:" in desc and "abs:" in desc
    assert "categor" in desc.lower()
    assert "YYYY-MM-DD" in desc
    assert "relevance" in desc and "date" in desc
    assert "next_start" in desc
    assert "abstract_mode" in desc
    assert "snippet" in desc


def test_search_papers_description_reduced_vs_baseline():
    """Description must shrink >=40% vs main @94a1317 baseline (3241 chars)."""
    desc_len = len(search_tool.description)
    reduction = 1.0 - (desc_len / _SEARCH_PAPERS_DESC_BASELINE_CHARS)
    assert reduction >= _SEARCH_PAPERS_DESC_REDUCTION_TARGET, (
        f"search_papers description is {desc_len} chars "
        f"({reduction:.1%} reduction); need >= "
        f"{_SEARCH_PAPERS_DESC_REDUCTION_TARGET:.0%} vs baseline "
        f"{_SEARCH_PAPERS_DESC_BASELINE_CHARS}"
    )


async def test_tools_list_serialized_size_snapshot():
    """Document tools/list payload size for PR before/after comparison."""
    tools = await list_tools()
    payload = [
        (
            t.model_dump(by_alias=True, exclude_none=True)
            if hasattr(t, "model_dump")
            else t.dict()
        )
        for t in tools
    ]
    total_chars = len(json.dumps(payload, separators=(",", ":")))
    search = next(t for t in tools if t.name == "search_papers")
    search_chars = len(
        json.dumps(
            (
                search.model_dump(by_alias=True, exclude_none=True)
                if hasattr(search, "model_dump")
                else search.dict()
            ),
            separators=(",", ":"),
        )
    )
    # Soft ceiling: after shrinking search_papers, total list should stay well
    # under the pre-change ~16k measurement on main @94a1317.
    assert search_chars < 3000
    # Soft ceiling allows abstract_mode (#128) and markdown outline tools (#129)
    # on top of the #131 shrink.
    assert total_chars < 17500
    assert len(search.description) < 1500
