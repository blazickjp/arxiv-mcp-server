"""Tests for citation graph tool."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arxiv_mcp_server.tools import citation_graph as citation_graph_module
from arxiv_mcp_server.tools.citation_graph import handle_citation_graph

PAPER_PAYLOAD = {
    "paperId": "root-paper",
    "title": "Root Paper",
    "year": 2024,
    "authors": [{"name": "Author A"}],
    "externalIds": {"ArXiv": "2401.12345"},
    "citationCount": 12,
    "referenceCount": 3,
}

CITATIONS_PAYLOAD = {
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
    ]
}

REFERENCES_PAYLOAD = {
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
    ]
}


def _json_response(payload, status_code=200, headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = payload
    if status_code >= 400 and status_code != 429:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    else:
        response.raise_for_status = MagicMock()
    return response


def _mock_async_client(responses):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=list(responses))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _success_responses():
    return [
        _json_response(PAPER_PAYLOAD),
        _json_response(CITATIONS_PAYLOAD),
        _json_response(REFERENCES_PAYLOAD),
    ]


@pytest.mark.asyncio
async def test_citation_graph_success():
    """Citation graph should return citations and references with normalized fields."""
    mock_client = _mock_async_client(_success_responses())

    with patch("httpx.AsyncClient", return_value=mock_client):
        response = await handle_citation_graph({"paper_id": "2401.12345"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["citation_count"] == 1
    assert payload["reference_count"] == 1
    assert payload["citation_total"] == 12
    assert payload["max_citations"] == 50
    assert payload["citations"][0]["arxiv_id"] == "2501.00001"
    assert mock_client.get.call_count == 3
    citation_call = mock_client.get.call_args_list[1]
    assert citation_call.args[0].endswith("/citations")
    assert citation_call.kwargs["params"]["limit"] == 50
    assert "externalIds" in citation_call.kwargs["params"]["fields"]
    assert payload["citations"][0]["external_ids"]["ArXiv"] == "2501.00001"
    assert payload["references"][0]["arxiv_id"] == "2001.00001"
    assert payload["references"][0]["external_ids"]["ArXiv"] == "2001.00001"


@pytest.mark.asyncio
async def test_citation_graph_http_error():
    """Citation graph should surface HTTP API errors without leaking S2 URLs."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Client error '500 Internal Server Error' for url "
        "'https://api.semanticscholar.org/graph/v1/paper/ARXIV:2401.12345'",
        request=MagicMock(),
        response=mock_response,
    )

    mock_client = _mock_async_client([mock_response])

    with patch("httpx.AsyncClient", return_value=mock_client):
        response = await handle_citation_graph({"paper_id": "2401.12345"})

    assert response[0].text.startswith("Error:")
    assert "semanticscholar.org" not in response[0].text
    assert "https://" not in response[0].text
    assert "HTTP 500" in response[0].text


@pytest.mark.asyncio
async def test_citation_graph_retries_on_429_then_succeeds():
    """A transient Semantic Scholar 429 should be retried and then succeed."""
    responses = [_json_response({}, status_code=429), *_success_responses()]
    mock_client = _mock_async_client(responses)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(
            citation_graph_module.asyncio, "sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        response = await handle_citation_graph({"paper_id": "1706.03762"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["paper"]["arxiv_id"] == "1706.03762"
    assert mock_client.get.call_count == 4
    sleep.assert_awaited()


@pytest.mark.asyncio
async def test_citation_graph_429_exhausted_has_useful_error():
    """Persistent 429s should tell the caller how to raise the S2 quota."""
    rate_limited = _json_response({}, status_code=429)
    mock_client = _mock_async_client([rate_limited] * 4)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module.asyncio, "sleep", new_callable=AsyncMock),
    ):
        response = await handle_citation_graph({"paper_id": "2608.18261"})

    assert response[0].text == (
        "Error: Semantic Scholar rate-limited; set SEMANTIC_SCHOLAR_API_KEY "
        "or try again later"
    )
    assert mock_client.get.call_count == 4


@pytest.mark.asyncio
async def test_citation_graph_sends_api_key_and_honors_max_citations():
    """Optional API key and max_citations should be forwarded to Semantic Scholar."""
    mock_client = _mock_async_client(_success_responses())

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(
            citation_graph_module.settings, "SEMANTIC_SCHOLAR_API_KEY", "s2-key"
        ),
    ):
        response = await handle_citation_graph(
            {"paper_id": "2401.12345", "max_citations": 10}
        )

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["max_citations"] == 10
    for call in mock_client.get.call_args_list:
        assert call.kwargs["headers"]["x-api-key"] == "s2-key"
    assert mock_client.get.call_args_list[1].kwargs["params"]["limit"] == 10


@pytest.mark.asyncio
async def test_citation_graph_neighbors_include_arxiv_id_from_external_ids():
    """Neighbors with ArXiv in externalIds must surface arxiv_id for tool hops."""
    citations = {
        "data": [
            {
                "citingPaper": {
                    "paperId": "sticky-routing",
                    "title": "Sticky Routing",
                    "year": 2026,
                    "authors": [{"name": "Author S"}],
                    "externalIds": {"ArXiv": "2607.08780"},
                }
            }
        ]
    }
    references = {
        "data": [
            {
                "citedPaper": {
                    "paperId": "promoe",
                    "title": "ProMoE",
                    "year": 2024,
                    "authors": [{"name": "Author P"}],
                    "externalIds": {"ArXiv": "2410.22134"},
                }
            }
        ]
    }
    mock_client = _mock_async_client(
        [
            _json_response(PAPER_PAYLOAD),
            _json_response(citations),
            _json_response(references),
        ]
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        response = await handle_citation_graph({"paper_id": "2505.16056"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["citations"][0]["arxiv_id"] == "2607.08780"
    assert payload["references"][0]["arxiv_id"] == "2410.22134"
    neighbor_fields = mock_client.get.call_args_list[1].kwargs["params"]["fields"]
    assert neighbor_fields == citation_graph_module.NEIGHBOR_FIELDS
    assert "externalIds" in neighbor_fields


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paper_id,expected_version",
    [("1706.03762v1", "v1"), ("1706.03762v7", "v7")],
)
async def test_citation_graph_strips_version_for_s2_lookup(paper_id, expected_version):
    """Versioned arXiv IDs must query Semantic Scholar with the bare ARXIV id."""
    paper_payload = {
        **PAPER_PAYLOAD,
        "externalIds": {"ArXiv": "1706.03762"},
    }
    mock_client = _mock_async_client(
        [
            _json_response(paper_payload),
            _json_response(CITATIONS_PAYLOAD),
            _json_response(REFERENCES_PAYLOAD),
        ]
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        response = await handle_citation_graph({"paper_id": paper_id})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["paper"]["arxiv_id"] == "1706.03762"
    assert payload["paper"]["requested_arxiv_id"] == paper_id
    assert payload["paper"]["requested_version"] == expected_version

    paper_url = mock_client.get.call_args_list[0].args[0]
    assert paper_url.endswith("/paper/ARXIV:1706.03762")
    assert expected_version not in paper_url.split("/paper/", 1)[1]
    assert (
        mock_client.get.call_args_list[1]
        .args[0]
        .endswith("/paper/ARXIV:1706.03762/citations")
    )
    assert (
        mock_client.get.call_args_list[2]
        .args[0]
        .endswith("/paper/ARXIV:1706.03762/references")
    )


@pytest.mark.asyncio
async def test_citation_graph_bare_id_omits_requested_version_fields():
    """Bare IDs should not invent requested_version metadata."""
    mock_client = _mock_async_client(_success_responses())

    with patch("httpx.AsyncClient", return_value=mock_client):
        response = await handle_citation_graph({"paper_id": "2401.12345"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["paper"]["arxiv_id"] == "2401.12345"
    assert "requested_arxiv_id" not in payload["paper"]
    assert "requested_version" not in payload["paper"]
    assert mock_client.get.call_args_list[0].args[0].endswith("/paper/ARXIV:2401.12345")


@pytest.mark.asyncio
async def test_citation_graph_404_does_not_leak_s2_url():
    """404 responses must not expose the Semantic Scholar request URL."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Client error '404 Not Found' for url "
        "'https://api.semanticscholar.org/graph/v1/paper/ARXIV:1706.03762v1'",
        request=MagicMock(),
        response=mock_response,
    )
    mock_client = _mock_async_client([mock_response])

    with patch("httpx.AsyncClient", return_value=mock_client):
        response = await handle_citation_graph({"paper_id": "1706.03762v1"})

    assert response[0].text == "Error: paper not found on Semantic Scholar"
    assert "semanticscholar.org" not in response[0].text
    assert "https://" not in response[0].text
