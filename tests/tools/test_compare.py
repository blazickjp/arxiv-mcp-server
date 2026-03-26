"""Tests for paper comparison tool."""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

import arxiv

from arxiv_mcp_server.tools.compare import handle_compare
from arxiv_mcp_server.clients.s2_client import S2Client


class MockAuthor:
    def __init__(self, name):
        self.name = name


def _make_mock_paper(paper_id, title, abstract="Test abstract"):
    """Create a mock arxiv.Result."""
    paper = MagicMock(spec=arxiv.Result)
    paper.get_short_id.return_value = paper_id
    paper.title = title
    paper.authors = [MockAuthor("Alice"), MockAuthor("Bob")]
    paper.summary = abstract
    paper.categories = ["cs.AI"]
    paper.published = datetime(2024, 1, 15, tzinfo=timezone.utc)
    paper.pdf_url = f"https://arxiv.org/pdf/{paper_id}"
    return paper


@pytest.mark.asyncio
async def test_compare_two_papers():
    """Test comparing two papers returns comparison JSON with both papers."""
    paper_a = _make_mock_paper("2401.11111", "Paper A", "Deep learning for NLP tasks")
    paper_b = _make_mock_paper("2401.22222", "Paper B", "Deep learning for vision tasks")

    mock_client = MagicMock(spec=arxiv.Client)
    mock_client.results.return_value = [paper_a, paper_b]

    with (
        patch("arxiv_mcp_server.tools.compare.arxiv.Client", return_value=mock_client),
        patch.object(
            S2Client,
            "batch_get_papers",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await handle_compare(
            {"paper_ids": ["2401.11111", "2401.22222"]}
        )

    assert len(result) == 1
    content = json.loads(result[0].text)

    comparison = content["comparison"]
    assert comparison["paper_count"] == 2
    assert len(comparison["papers"]) == 2
    assert comparison["papers"][0]["title"] == "Paper A"
    assert comparison["papers"][1]["title"] == "Paper B"
    assert "keyword_overlap" in comparison
    assert "markdown_table" in content


@pytest.mark.asyncio
async def test_compare_with_citation_counts():
    """Test comparison enriches papers with citation counts from S2."""
    paper_a = _make_mock_paper("2401.11111", "Paper A")
    paper_b = _make_mock_paper("2401.22222", "Paper B")

    mock_client = MagicMock(spec=arxiv.Client)
    mock_client.results.return_value = [paper_a, paper_b]

    s2_batch_result = [
        {
            "paperId": "s2_aaa",
            "externalIds": {"ArXiv": "2401.11111"},
            "citationCount": 100,
        },
        {
            "paperId": "s2_bbb",
            "externalIds": {"ArXiv": "2401.22222"},
            "citationCount": 50,
        },
    ]

    with (
        patch("arxiv_mcp_server.tools.compare.arxiv.Client", return_value=mock_client),
        patch.object(
            S2Client,
            "batch_get_papers",
            new_callable=AsyncMock,
            return_value=s2_batch_result,
        ),
    ):
        result = await handle_compare(
            {"paper_ids": ["2401.11111", "2401.22222"]}
        )

    content = json.loads(result[0].text)
    papers = content["comparison"]["papers"]
    assert papers[0]["citation_count"] == 100
    assert papers[1]["citation_count"] == 50


@pytest.mark.asyncio
async def test_compare_invalid_count():
    """Test that fewer than 2 paper IDs returns an error."""
    result = await handle_compare({"paper_ids": ["2401.11111"]})

    assert len(result) == 1
    assert "Error" in result[0].text
    assert "At least 2" in result[0].text
