"""Tests for citation graph tool."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arxiv_mcp_server.tools.citation_graph import handle_citation_graph


@pytest.mark.asyncio
async def test_citation_graph_success():
    """Citation graph should return citations and references with normalized fields."""
    mock_payload = {
        "paperId": "root-paper",
        "title": "Root Paper",
        "year": 2024,
        "authors": [{"name": "Author A"}],
        "externalIds": {"ArXiv": "2401.12345"},
        "citations": [
            {
                "paperId": "citing-1",
                "title": "Citing Paper",
                "year": 2025,
                "authors": [{"name": "Author B"}],
                "externalIds": {"ArXiv": "2501.00001"},
            }
        ],
        "references": [
            {
                "paperId": "ref-1",
                "title": "Referenced Paper",
                "year": 2020,
                "authors": [{"name": "Author C"}],
                "externalIds": {"ArXiv": "2001.00001"},
            }
        ],
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = mock_payload

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["citation_count"] == 1
    assert payload["reference_count"] == 1
    assert payload["citations"][0]["arxiv_id"] == "2501.00001"


@pytest.mark.asyncio
async def test_citation_graph_http_error():
    """Citation graph should surface HTTP API errors."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("boom")

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    assert response[0].text.startswith("Error:")
