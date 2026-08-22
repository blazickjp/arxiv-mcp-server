"""MCP-level assertion that default read_paper responses stay bounded (#127)."""

import json
from pathlib import Path

import mcp.types as types
import pytest

from arxiv_mcp_server import server as server_module
from arxiv_mcp_server.tools import read_paper as read_module
from arxiv_mcp_server.tools.content import DEFAULT_MAX_CHARS
from arxiv_mcp_server.tools.download import download_tool
from arxiv_mcp_server.tools.read_paper import read_tool


@pytest.mark.asyncio
async def test_mcp_read_paper_default_response_is_size_bounded(
    temp_storage_path, monkeypatch
):
    """A default CallToolRequest must not emit an unbounded paper body.

    Exercises the real MCP request handler (stdio transport path) and asserts
    both pagination metadata and raw JSON response size stay bounded.
    """
    monkeypatch.setattr(
        read_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    paper_id = "1706.03762"
    # Simulate a large cached paper (~50k chars) well above the default bound.
    content = ("Attention is all you need. " * 2000)[:50_000]
    assert len(content) > DEFAULT_MAX_CHARS
    Path(temp_storage_path, f"{paper_id}.md").write_text(content, encoding="utf-8")

    handler = server_module.server.request_handlers[types.CallToolRequest]
    result = await handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(
                name="read_paper", arguments={"paper_id": paper_id}
            )
        )
    )

    assert result.root.isError is False
    raw = result.root.content[0].text
    payload = json.loads(raw)

    assert payload["status"] == "success"
    assert payload["content_length"] == len(content)
    assert payload["returned_chars"] == DEFAULT_MAX_CHARS
    assert payload["is_truncated"] is True
    assert payload["next_start"] == DEFAULT_MAX_CHARS
    assert "next_retrieval" in payload

    # Paper body is bounded; untrusted notice is a separate first-page field (#215).
    assert len(payload["content"]) == DEFAULT_MAX_CHARS
    assert "UNTRUSTED EXTERNAL CONTENT" in payload["content_warning"]
    assert "UNTRUSTED" not in payload["content"]

    # Whole MCP text payload must stay well under a naive full-paper dump.
    # Full paper JSON would be >50k; default chunk should be roughly warning +
    # 12k + small metadata overhead.
    assert len(raw) < 30_000
    assert len(raw) < len(content)


def test_download_and_read_tool_schemas_document_bounded_default():
    """Tool descriptions and schemas must advertise the default + opt-in."""
    for tool in (download_tool, read_tool):
        assert "12,000" in tool.description or "12000" in tool.description
        assert "return_full_text" in tool.description
        assert "next_start" in tool.description
        props = tool.inputSchema["properties"]
        assert "return_full_text" in props
        assert props["return_full_text"]["type"] == "boolean"
        assert "12,000" in props["max_chars"]["description"]
        assert "full content" not in props["max_chars"]["description"].lower()
