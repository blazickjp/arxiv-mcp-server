"""Unit tests for prompt handlers."""

import pytest
from typing import Dict
from arxiv_mcp_server.prompts.handlers import list_prompts, get_prompt
from mcp.types import GetPromptResult, PromptMessage, TextContent


@pytest.mark.asyncio
async def test_list_prompts():
    """Test listing available prompts."""
    prompts = await list_prompts()
    assert len(prompts) == 1

    prompt_names = {p.name for p in prompts}
    expected_names = {"deep-paper-analysis"}
    assert prompt_names == expected_names


@pytest.mark.asyncio
async def test_get_paper_analysis_prompt():
    """Test getting paper analysis prompt."""
    result = await get_prompt("deep-paper-analysis", {"paper_id": "2401.00123"})

    assert isinstance(result, GetPromptResult)
    assert len(result.messages) == 1
    message = result.messages[0]

    assert isinstance(message, PromptMessage)
    assert message.role == "user"
    assert isinstance(message.content, TextContent)
    assert "2401.00123" in message.content.text


@pytest.mark.asyncio
async def test_get_prompt_with_invalid_name():
    """Test getting prompt with invalid name."""
    with pytest.raises(ValueError, match="Prompt not found"):
        await get_prompt("invalid-prompt", {})


@pytest.mark.asyncio
async def test_get_prompt_with_no_arguments():
    """Test getting prompt with no arguments."""
    with pytest.raises(ValueError, match="No arguments provided"):
        await get_prompt("deep-paper-analysis", None)


@pytest.mark.asyncio
async def test_get_prompt_with_missing_required_argument():
    """Test getting prompt with missing required argument."""
    with pytest.raises(ValueError, match="Missing required argument"):
        await get_prompt("deep-paper-analysis", {})
