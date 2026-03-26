"""Tests for the research_memory MCP tool."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from research_mcp_server.tools.research_memory_tools import (
    handle_research_memory,
    research_memory_tool,
)

MODULE = "research_mcp_server.tools.research_memory_tools"


def _parse(result):
    """Extract parsed JSON from a tool result list."""
    assert len(result) == 1
    return json.loads(result[0].text)


class TestToolDefinition:
    def test_tool_definition_exists(self):
        assert research_memory_tool is not None
        assert research_memory_tool.name == "research_memory"
        schema = research_memory_tool.inputSchema
        assert "action" in schema["properties"]
        actions = schema["properties"]["action"]["enum"]
        assert "create_session" in actions
        assert "warm_context" in actions
        assert "save_digest" in actions
        assert "list_theses" in actions
        assert schema["required"] == ["action"]


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_create_session(self):
        with patch(f"{MODULE}._memory") as mock_memory:
            mock_memory.create_session = AsyncMock(return_value="sess-123")
            result = await handle_research_memory(
                {"action": "create_session", "name": "Test Session", "goal": "Find papers"}
            )
            data = _parse(result)
            assert data["session_id"] == "sess-123"
            assert data["status"] == "created"
            mock_memory.create_session.assert_awaited_once_with(
                name="Test Session", goal="Find papers"
            )

    @pytest.mark.asyncio
    async def test_create_session_missing_name(self):
        result = await handle_research_memory({"action": "create_session"})
        data = _parse(result)
        assert "error" in data


class TestAddThesis:
    @pytest.mark.asyncio
    async def test_add_thesis(self):
        with patch(f"{MODULE}._memory") as mock_memory:
            mock_memory.add_thesis = AsyncMock(return_value="thesis-456")
            result = await handle_research_memory(
                {
                    "action": "add_thesis",
                    "statement": "Transformers scale better",
                    "category": "primary",
                    "confidence": 0.8,
                }
            )
            data = _parse(result)
            assert data["thesis_id"] == "thesis-456"
            assert data["status"] == "created"
            mock_memory.add_thesis.assert_awaited_once_with(
                statement="Transformers scale better",
                category="primary",
                confidence=0.8,
            )

    @pytest.mark.asyncio
    async def test_add_thesis_missing_statement(self):
        result = await handle_research_memory({"action": "add_thesis"})
        data = _parse(result)
        assert "error" in data


class TestWarmContext:
    @pytest.mark.asyncio
    async def test_get_warm_context(self):
        mock_context = {
            "total_prior_runs": 5,
            "latest_digest": {"content": "Summary of findings"},
            "active_theses": [
                {"id": "t1", "statement": "LLMs are cool", "confidence": 0.9}
            ],
        }
        with patch(f"{MODULE}._memory") as mock_memory:
            mock_memory.get_warm_context = AsyncMock(return_value=mock_context)
            result = await handle_research_memory({"action": "warm_context"})
            data = _parse(result)
            assert data["total_prior_runs"] == 5
            assert data["active_theses"][0]["statement"] == "LLMs are cool"
            mock_memory.get_warm_context.assert_awaited_once()


class TestUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await handle_research_memory({"action": "nonexistent"})
        data = _parse(result)
        assert "error" in data
        assert "Unknown action" in data["error"]
