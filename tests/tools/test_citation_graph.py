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


def _legacy_mock_payload():
    """Shared legacy nested payload (mirrors test_citation_graph_success)."""
    return {
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


@pytest.mark.asyncio
async def test_citation_graph_default_unchanged():
    """Default call (no new params) must still take the legacy nested path.

    Asserts: indent=2 output, edges include authors + external_ids, single
    nested request (one client.get).
    """
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _legacy_mock_payload()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    text = response[0].text
    # Legacy path uses indent=2 -> newlines present in the rendered JSON.
    assert "\n" in text
    # Legacy path makes exactly one (nested) request.
    assert mock_client.get.await_count == 1

    payload = json.loads(text)
    assert "pagination" not in payload
    citation_edge = payload["citations"][0]
    assert "authors" in citation_edge
    assert "external_ids" in citation_edge
    assert citation_edge["arxiv_id"] == "2501.00001"
    reference_edge = payload["references"][0]
    assert "authors" in reference_edge
    assert "external_ids" in reference_edge


@pytest.mark.asyncio
async def test_citation_graph_compact():
    """Compact opt-in path: minified output, stripped edges, pagination block."""
    # Call order in the implementation: root metadata, /citations, /references.
    root_response = MagicMock()
    root_response.raise_for_status = MagicMock()
    root_response.json.return_value = {
        "paperId": "root-paper",
        "title": "Root Paper",
        "year": 2024,
        "authors": [{"name": "Author A"}],
        "externalIds": {"ArXiv": "2401.12345"},
    }

    citations_response = MagicMock()
    citations_response.raise_for_status = MagicMock()
    citations_response.json.return_value = {
        "offset": 0,
        "next": 5,
        "data": [
            {
                "citingPaper": {
                    "paperId": "citing-1",
                    "title": "Citing Paper",
                    "year": 2025,
                    "authors": [{"name": "Author B"}],
                    "externalIds": {"ArXiv": "2501.00001"},
                }
            }
        ],
    }

    references_response = MagicMock()
    references_response.raise_for_status = MagicMock()
    references_response.json.return_value = {
        "offset": 0,
        "next": 5,
        "data": [
            {
                "citedPaper": {
                    "paperId": "ref-1",
                    "title": "Referenced Paper",
                    "year": 2020,
                    "authors": [{"name": "Author C"}],
                    "externalIds": {"ArXiv": "2001.00001"},
                }
            }
        ],
    }

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[root_response, citations_response, references_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph(
            {"paper_id": "2401.12345", "compact": True, "limit": 5}
        )

    text = response[0].text
    # Minified output: no newline.
    assert "\n" not in text

    payload = json.loads(text)
    assert payload["status"] == "success"

    citation_edge = payload["citations"][0]
    assert set(citation_edge.keys()) == {"paper_id", "arxiv_id", "title", "year"}
    assert citation_edge["arxiv_id"] == "2501.00001"

    reference_edge = payload["references"][0]
    assert set(reference_edge.keys()) == {"paper_id", "arxiv_id", "title", "year"}

    # Compact root paper has no authors/external_ids.
    assert set(payload["paper"].keys()) == {"paper_id", "arxiv_id", "title", "year"}

    assert "pagination" in payload
    assert payload["pagination"]["limit"] == 5
    assert payload["pagination"]["citations"]["offset"] == 0
    assert payload["pagination"]["references"]["offset"] == 0
    assert payload["pagination"]["citations"]["next"] == 5
    assert payload["pagination"]["citations"]["returned"] == 1


@pytest.mark.asyncio
async def test_citation_graph_paginated_full():
    """Paginated non-compact path: full edges, indent=2, offset propagated."""
    # Call order in the implementation: root metadata, /citations, /references.
    root_response = MagicMock()
    root_response.raise_for_status = MagicMock()
    root_response.json.return_value = {
        "paperId": "root-paper",
        "title": "Root Paper",
        "year": 2024,
        "authors": [{"name": "Author A"}],
        "externalIds": {"ArXiv": "2401.12345"},
    }

    citations_response = MagicMock()
    citations_response.raise_for_status = MagicMock()
    citations_response.json.return_value = {
        "offset": 5,
        "next": 10,
        "data": [
            {
                "citingPaper": {
                    "paperId": "citing-1",
                    "title": "Citing Paper",
                    "year": 2025,
                    "authors": [{"name": "Author B"}],
                    "externalIds": {"ArXiv": "2501.00001"},
                }
            }
        ],
    }

    references_response = MagicMock()
    references_response.raise_for_status = MagicMock()
    references_response.json.return_value = {
        "offset": 5,
        "data": [
            {
                "citedPaper": {
                    "paperId": "ref-1",
                    "title": "Referenced Paper",
                    "year": 2020,
                    "authors": [{"name": "Author C"}],
                    "externalIds": {"ArXiv": "2001.00001"},
                }
            }
        ],
    }

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[root_response, citations_response, references_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph(
            {"paper_id": "2401.12345", "limit": 5, "offset": 5}
        )

    text = response[0].text
    # Non-compact path uses indent=2 -> newlines present.
    assert "\n" in text

    payload = json.loads(text)
    assert payload["pagination"]["citations"]["offset"] == 5
    assert payload["pagination"]["references"]["offset"] == 5
    assert payload["pagination"]["limit"] == 5

    citation_edge = payload["citations"][0]
    assert "authors" in citation_edge
    assert citation_edge["authors"] == ["Author B"]
    assert "external_ids" in citation_edge

    # next absent on last page -> None.
    assert payload["pagination"]["references"]["next"] is None


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
