"""Integration tests for prompt functionality."""

import pytest
from arxiv_mcp_server.prompts.handlers import list_prompts, get_prompt


@pytest.mark.asyncio
async def test_server_list_prompts():
    """Test server list_prompts endpoint."""
    prompts = await list_prompts()
    assert len(prompts) == 1

    # Check that all prompts have required fields
    for prompt in prompts:
        assert prompt.name
        assert prompt.description
        assert prompt.arguments is not None


@pytest.mark.asyncio
async def test_server_get_analysis_prompt():
    """Test server get_prompt endpoint with analysis prompt."""
    result = await get_prompt("deep-paper-analysis", {"paper_id": "2401.00123"})

    assert len(result.messages) == 1
    message = result.messages[0]
    assert message.role == "user"
    assert "2401.00123" in message.content.text


@pytest.mark.asyncio
async def test_server_get_prompt_invalid_name():
    """Test server get_prompt endpoint with invalid prompt name."""
    with pytest.raises(ValueError, match="Prompt not found"):
        await get_prompt("invalid-prompt", {})


@pytest.mark.asyncio
async def test_server_get_prompt_missing_args():
    """Test server get_prompt endpoint with missing required arguments."""
    with pytest.raises(ValueError, match="Missing required argument"):
        await get_prompt("deep-paper-analysis", {})
