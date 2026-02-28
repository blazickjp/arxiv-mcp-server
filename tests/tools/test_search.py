"""Tests for paper search functionality."""

import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from arxiv_mcp_server.tools import handle_search
from arxiv_mcp_server.tools.search import (
    _validate_categories,
    _raw_arxiv_search,
    _parse_arxiv_atom_response,
)


@pytest.mark.asyncio
async def test_basic_search(mock_client):
    """Test basic paper search functionality."""
    with patch(
        "arxiv_mcp_server.tools.search.get_arxiv_client", return_value=mock_client
    ):
        result = await handle_search({"query": "test query", "max_results": 1})

        assert len(result) == 1
        content = json.loads(result[0].text)
        assert content["total_results"] == 1
        paper = content["papers"][0]
        assert paper["id"] == "2103.12345"
        assert paper["title"] == "Test Paper"
        assert "resource_uri" in paper


@pytest.mark.asyncio
async def test_search_with_categories(mock_client):
    """Test paper search with category filtering."""
    with patch(
        "arxiv_mcp_server.tools.search.get_arxiv_client", return_value=mock_client
    ):
        result = await handle_search(
            {"query": "test query", "categories": ["cs.AI", "cs.LG"], "max_results": 1}
        )

        content = json.loads(result[0].text)
        assert content["papers"][0]["categories"] == ["cs.AI", "cs.LG"]


@pytest.mark.asyncio
async def test_search_with_dates():
    """Test paper search with date filtering uses raw API."""
    mock_xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
        <entry>
            <id>http://arxiv.org/abs/2301.00001v1</id>
            <title>Test Paper</title>
            <summary>Test abstract</summary>
            <published>2023-06-15T00:00:00Z</published>
            <author><name>Test Author</name></author>
            <arxiv:primary_category term="cs.AI"/>
            <link title="pdf" href="http://arxiv.org/pdf/2301.00001v1"/>
        </entry>
    </feed>"""

    mock_response = MagicMock()
    mock_response.text = mock_xml_response
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await handle_search(
            {
                "query": "test query",
                "date_from": "2022-01-01",
                "date_to": "2024-01-01",
                "max_results": 1,
            }
        )

        content = json.loads(result[0].text)
        assert content["total_results"] == 1
        assert len(content["papers"]) == 1


@pytest.mark.asyncio
async def test_search_with_invalid_dates():
    """Test search with invalid date formats."""
    result = await handle_search(
        {"query": "test query", "date_from": "invalid-date", "max_results": 1}
    )

    assert "Error:" in result[0].text


def test_validate_categories():
    """Test category validation function."""
    # Valid categories
    assert _validate_categories(["cs.AI", "cs.LG"])
    assert _validate_categories(["math.CO", "physics.gen-ph"])

    # Invalid categories
    assert not _validate_categories(["invalid.category"])
    assert not _validate_categories(["cs.AI", "invalid.test"])


def test_parse_arxiv_atom_response():
    """Test parsing of arXiv Atom XML response."""
    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
        <entry>
            <id>http://arxiv.org/abs/2301.00001v1</id>
            <title>Test Paper Title</title>
            <summary>This is a test abstract.</summary>
            <published>2023-01-01T00:00:00Z</published>
            <author><name>John Doe</name></author>
            <author><name>Jane Smith</name></author>
            <arxiv:primary_category term="cs.AI"/>
            <category term="cs.AI"/>
            <category term="cs.LG"/>
            <link title="pdf" href="http://arxiv.org/pdf/2301.00001v1"/>
        </entry>
    </feed>"""

    results = _parse_arxiv_atom_response(sample_xml)
    assert len(results) == 1
    paper = results[0]
    assert paper["id"] == "2301.00001"
    assert paper["title"] == "Test Paper Title"
    assert paper["abstract"] == "This is a test abstract."
    assert paper["authors"] == ["John Doe", "Jane Smith"]
    assert "cs.AI" in paper["categories"]
    assert paper["resource_uri"] == "arxiv://2301.00001"


@pytest.mark.asyncio
async def test_raw_arxiv_search_builds_correct_url():
    """Test that raw search builds correct URL with date filters."""
    import httpx

    # Mock the httpx client
    mock_response = MagicMock()
    mock_response.text = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
    </feed>"""
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await _raw_arxiv_search(
            query="LLM",
            max_results=5,
            date_from="2023-01-01",
            date_to="2023-12-31",
            categories=["cs.AI"],
        )

        # Check that the URL was constructed with unencoded +TO+
        call_args = mock_client.get.call_args
        url = call_args[0][0]
        assert "+TO+" in url  # Critical: must not be encoded as %2B
        assert "submittedDate:" in url
        assert "20230101" in url
        assert "20231231" in url


@pytest.mark.asyncio
async def test_search_with_invalid_categories(mock_client):
    """Test search with invalid categories."""
    with patch(
        "arxiv_mcp_server.tools.search.get_arxiv_client", return_value=mock_client
    ):
        result = await handle_search(
            {
                "query": "test query",
                "categories": ["invalid.category"],
                "max_results": 1,
            }
        )

        assert "Error: Invalid category" in result[0].text


@pytest.mark.asyncio
async def test_search_empty_query(mock_client):
    """Test search with empty query but categories."""
    with patch(
        "arxiv_mcp_server.tools.search.get_arxiv_client", return_value=mock_client
    ):
        result = await handle_search(
            {"query": "", "categories": ["cs.AI"], "max_results": 1}
        )

        # Should still work with just categories
        content = json.loads(result[0].text)
        assert "papers" in content


@pytest.mark.asyncio
async def test_search_arxiv_error(mock_client):
    """Test handling of arXiv API errors."""
    import arxiv

    # Create proper ArxivError with required parameters
    error = arxiv.ArxivError("http://example.com", retry=3, message="API Error")
    mock_client.results.side_effect = error

    with patch(
        "arxiv_mcp_server.tools.search.get_arxiv_client", return_value=mock_client
    ):
        result = await handle_search({"query": "test", "max_results": 1})

        assert "ArXiv API error" in result[0].text


@pytest.mark.asyncio
async def test_search_max_results_limiting(mock_client):
    """Test that max_results is properly limited."""
    with patch(
        "arxiv_mcp_server.tools.search.get_arxiv_client", return_value=mock_client
    ):
        # Test that very large max_results gets capped
        result = await handle_search({"query": "test", "max_results": 1000})

        # Should not fail and should be limited by settings.MAX_RESULTS
        content = json.loads(result[0].text)
        assert "papers" in content


@pytest.mark.asyncio
async def test_search_sort_by_relevance(mock_client):
    """Test search with relevance sorting (default)."""
    with patch(
        "arxiv_mcp_server.tools.search.get_arxiv_client", return_value=mock_client
    ):
        result = await handle_search({"query": "test", "sort_by": "relevance"})

        content = json.loads(result[0].text)
        assert "papers" in content


@pytest.mark.asyncio
async def test_search_sort_by_date(mock_client):
    """Test search with date sorting."""
    with patch(
        "arxiv_mcp_server.tools.search.get_arxiv_client", return_value=mock_client
    ):
        result = await handle_search({"query": "test", "sort_by": "date"})

        content = json.loads(result[0].text)
        assert "papers" in content


@pytest.mark.asyncio
async def test_search_no_query_optimization(mock_client):
    """Test that queries are not automatically modified."""
    from arxiv_mcp_server.tools.search import _optimize_query

    # Test that complex queries are not mangled
    complex_query = "graph neural networks message passing attention mechanism"
    optimized = _optimize_query(complex_query)
    assert optimized == complex_query

    # Test that field-specific queries are preserved
    field_query = 'ti:"graph neural networks"'
    optimized = _optimize_query(field_query)
    assert optimized == field_query

    # Test that boolean queries are preserved
    bool_query = "machine learning AND deep learning"
    optimized = _optimize_query(bool_query)
    assert optimized == bool_query
