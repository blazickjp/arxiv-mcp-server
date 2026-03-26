"""Tests for export tool."""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

import arxiv

from research_mcp_server.tools.export import handle_export
from research_mcp_server.clients.s2_client import S2Client


class MockAuthor:
    def __init__(self, name):
        self.name = name


def _make_mock_paper(paper_id="2401.12345", title="Test Paper"):
    """Create a mock arxiv.Result."""
    paper = MagicMock(spec=arxiv.Result)
    paper.get_short_id.return_value = paper_id
    paper.title = title
    paper.authors = [MockAuthor("Alice"), MockAuthor("Bob")]
    paper.summary = "Test abstract about machine learning."
    paper.categories = ["cs.AI", "cs.LG"]
    paper.published = datetime(2024, 1, 15, tzinfo=timezone.utc)
    paper.pdf_url = f"https://arxiv.org/pdf/{paper_id}"
    return paper


@pytest.mark.asyncio
async def test_export_bibtex():
    """Test BibTeX format export contains @article entry."""
    mock_paper = _make_mock_paper()
    mock_client = MagicMock(spec=arxiv.Client)
    mock_client.results.return_value = [mock_paper]

    with patch("research_mcp_server.tools.export.arxiv.Client", return_value=mock_client):
        result = await handle_export(
            {"paper_ids": ["2401.12345"], "format": "bibtex"}
        )

    assert len(result) == 1
    text = result[0].text
    assert "@article" in text
    assert "Test Paper" in text
    assert "Alice" in text


@pytest.mark.asyncio
async def test_export_json():
    """Test JSON format export produces valid JSON."""
    mock_paper = _make_mock_paper()
    mock_client = MagicMock(spec=arxiv.Client)
    mock_client.results.return_value = [mock_paper]

    with patch("research_mcp_server.tools.export.arxiv.Client", return_value=mock_client):
        result = await handle_export(
            {"paper_ids": ["2401.12345"], "format": "json"}
        )

    text = result[0].text
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "Test Paper"
    assert parsed[0]["paper_id"] == "2401.12345"


@pytest.mark.asyncio
async def test_export_markdown():
    """Test markdown format export contains markdown headers."""
    mock_paper = _make_mock_paper()
    mock_client = MagicMock(spec=arxiv.Client)
    mock_client.results.return_value = [mock_paper]

    with patch("research_mcp_server.tools.export.arxiv.Client", return_value=mock_client):
        result = await handle_export(
            {"paper_ids": ["2401.12345"], "format": "markdown"}
        )

    text = result[0].text
    assert "###" in text
    assert "Test Paper" in text
    assert "**Authors**" in text


@pytest.mark.asyncio
async def test_export_with_citation_counts():
    """Test export with citation counts from Semantic Scholar."""
    mock_paper = _make_mock_paper()
    mock_client = MagicMock(spec=arxiv.Client)
    mock_client.results.return_value = [mock_paper]

    s2_batch_result = [
        {
            "paperId": "abc123",
            "externalIds": {"ArXiv": "2401.12345"},
            "citationCount": 42,
        }
    ]

    with (
        patch(
            "research_mcp_server.tools.export.arxiv.Client",
            return_value=mock_client,
        ),
        patch.object(
            S2Client,
            "batch_get_papers",
            new_callable=AsyncMock,
            return_value=s2_batch_result,
        ),
    ):
        result = await handle_export(
            {
                "paper_ids": ["2401.12345"],
                "format": "json",
                "include_citation_count": True,
            }
        )

    parsed = json.loads(result[0].text)
    assert parsed[0]["citation_count"] == 42
