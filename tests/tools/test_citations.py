"""Tests for citation graph tool."""

import json
import pytest
from unittest.mock import patch, AsyncMock

from research_mcp_server.tools.citations import handle_citation_graph
from research_mcp_server.clients.s2_client import S2Client


def _mock_root_paper():
    """Return a mock S2 paper dict."""
    return {
        "paperId": "abc123",
        "title": "Root Paper",
        "authors": [{"name": "Alice"}],
        "year": 2024,
        "venue": "NeurIPS",
        "abstract": "A root paper abstract.",
        "citationCount": 10,
        "influentialCitationCount": 3,
        "referenceCount": 25,
        "fieldsOfStudy": ["Computer Science"],
        "isOpenAccess": True,
        "publicationDate": "2024-01-15",
    }


def _mock_citing_papers():
    """Return mock citing papers."""
    return [
        {"paperId": "cite1", "title": "Citing Paper 1", "year": 2024},
        {"paperId": "cite2", "title": "Citing Paper 2", "year": 2024},
    ]


def _mock_reference_papers():
    """Return mock reference papers."""
    return [
        {"paperId": "ref1", "title": "Reference Paper 1", "year": 2023},
    ]


@pytest.mark.asyncio
async def test_citation_graph_both():
    """Test citation graph with direction='both' returns root, citations, and references."""
    with (
        patch.object(
            S2Client, "get_paper", new_callable=AsyncMock, return_value=_mock_root_paper()
        ),
        patch.object(
            S2Client, "get_citations", new_callable=AsyncMock, return_value=_mock_citing_papers()
        ),
        patch.object(
            S2Client, "get_references", new_callable=AsyncMock, return_value=_mock_reference_papers()
        ),
    ):
        result = await handle_citation_graph(
            {"paper_id": "2401.12345", "direction": "both"}
        )

    assert len(result) == 1
    content = json.loads(result[0].text)

    assert content["root_paper"]["title"] == "Root Paper"
    assert content["root_paper"]["citationCount"] == 10
    assert content["stats"]["total_citations"] == 10
    assert content["stats"]["reference_count"] == 25

    assert len(content["citations"]) == 2
    assert content["citations"][0]["title"] == "Citing Paper 1"
    assert len(content["references"]) == 1
    assert content["references"][0]["title"] == "Reference Paper 1"


@pytest.mark.asyncio
async def test_citation_graph_paper_not_found():
    """Test citation graph when S2Client raises ValueError for unfound paper."""
    with patch.object(
        S2Client,
        "get_paper",
        new_callable=AsyncMock,
        side_effect=ValueError("Paper not found on Semantic Scholar."),
    ):
        result = await handle_citation_graph({"paper_id": "9999.99999"})

    assert len(result) == 1
    content = json.loads(result[0].text)
    assert content["error"] == "paper_not_found"
    assert "Paper not found" in content["message"]


@pytest.mark.asyncio
async def test_citation_graph_citations_only():
    """Test citation graph with direction='citations' only fetches citations."""
    with (
        patch.object(
            S2Client, "get_paper", new_callable=AsyncMock, return_value=_mock_root_paper()
        ),
        patch.object(
            S2Client, "get_citations", new_callable=AsyncMock, return_value=_mock_citing_papers()
        ) as mock_citations,
        patch.object(
            S2Client, "get_references", new_callable=AsyncMock
        ) as mock_references,
    ):
        result = await handle_citation_graph(
            {"paper_id": "2401.12345", "direction": "citations"}
        )

    content = json.loads(result[0].text)

    assert "citations" in content
    assert len(content["citations"]) == 2
    assert "references" not in content

    mock_citations.assert_called_once()
    mock_references.assert_not_called()
