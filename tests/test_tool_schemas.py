"""Tool schema compatibility tests."""

from arxiv_mcp_server.server import list_tools


async def test_tool_input_schemas_are_closed():
    """MCP clients expect tool schemas to reject unknown arguments."""
    tools = await list_tools()

    assert tools
    for tool in tools:
        assert tool.inputSchema["type"] == "object"
        assert (
            tool.inputSchema.get("additionalProperties") is False
        ), f"{tool.name} inputSchema must set additionalProperties=False"
