"""MCP protocol-level error semantics for tool calls."""

from unittest.mock import AsyncMock

import pytest
import mcp.types as types

from arxiv_mcp_server import server as server_module

NO_ENTRIES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query: id_list=0000.00000</title>
</feed>
"""


class MockResponse:
    def __init__(self, text: str):
        self.text = text


@pytest.mark.asyncio
async def test_get_abstract_not_found_sets_mcp_is_error(mocker):
    """JSON error payloads from get_abstract should be MCP tool errors."""
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        AsyncMock(return_value=MockResponse(NO_ENTRIES_XML)),
    )

    handler = server_module.server.request_handlers[types.CallToolRequest]
    result = await handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(
                name="get_abstract", arguments={"paper_id": "0000.00000"}
            )
        )
    )

    assert result.root.isError is True
    assert "Paper 0000.00000 not found on arXiv" in result.root.content[0].text
