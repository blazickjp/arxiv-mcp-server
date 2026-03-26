"""Tests for advanced query tool."""

import json
import pytest
from unittest.mock import patch, AsyncMock

from research_mcp_server.tools.advanced_query import handle_advanced_query


@pytest.mark.asyncio
async def test_advanced_query_by_title():
    """Test advanced query searching by title field."""
    mock_results = [
        {
            "id": "2401.12345",
            "title": "Test Paper on Transformers",
            "authors": ["Alice"],
            "abstract": "A paper about transformers.",
            "categories": ["cs.AI"],
            "published": "2024-01-15T00:00:00Z",
            "url": "https://arxiv.org/pdf/2401.12345",
        }
    ]

    with patch(
        "research_mcp_server.tools.advanced_query.advanced_search",
        new_callable=AsyncMock,
        return_value=mock_results,
    ) as mock_search:
        result = await handle_advanced_query({"title": "transformers"})

        assert len(result) == 1
        content = json.loads(result[0].text)
        assert content["total_results"] == 1
        assert content["papers"][0]["title"] == "Test Paper on Transformers"
        assert content["papers"][0]["id"] == "2401.12345"

        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args
        assert call_kwargs.kwargs["title"] == "transformers"


@pytest.mark.asyncio
async def test_advanced_query_combined_fields():
    """Test advanced query with title, author, and categories combined."""
    mock_results = [
        {
            "id": "2401.99999",
            "title": "Attention Is All You Need",
            "authors": ["Vaswani"],
            "abstract": "Transformer architecture.",
            "categories": ["cs.CL"],
            "published": "2024-01-01T00:00:00Z",
            "url": "https://arxiv.org/pdf/2401.99999",
        }
    ]

    with patch(
        "research_mcp_server.tools.advanced_query.advanced_search",
        new_callable=AsyncMock,
        return_value=mock_results,
    ) as mock_search:
        result = await handle_advanced_query(
            {
                "title": "attention",
                "author": "Vaswani",
                "categories": ["cs.CL", "cs.AI"],
                "max_results": 5,
            }
        )

        content = json.loads(result[0].text)
        assert content["total_results"] == 1
        assert content["papers"][0]["authors"] == ["Vaswani"]

        call_kwargs = mock_search.call_args
        assert call_kwargs.kwargs["title"] == "attention"
        assert call_kwargs.kwargs["author"] == "Vaswani"
        assert call_kwargs.kwargs["categories"] == ["cs.CL", "cs.AI"]
        assert call_kwargs.kwargs["max_results"] == 5


@pytest.mark.asyncio
async def test_advanced_query_no_criteria():
    """Test that providing no search criteria returns an error."""
    result = await handle_advanced_query({})

    assert len(result) == 1
    assert "Error" in result[0].text
    assert "At least one search field" in result[0].text
