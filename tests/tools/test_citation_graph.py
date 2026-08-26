"""Tests for citation graph tool."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arxiv_mcp_server.tools import citation_graph as citation_graph_module
from arxiv_mcp_server.tools.citation_graph import handle_citation_graph

# Updated payload to include citations and references in the paper response
PAPER_PAYLOAD_WITH_CITATIONS_AND_REFERENCES = {
    "paperId": "root-paper",
    "title": "Root Paper",
    "year": 2024,
    "authors": [{"name": "Author A"}],
    "externalIds": {"ArXiv": "2401.12345"},
    "citationCount": 12,
    "referenceCount": 3,
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


def _success_response():
    """Return a single response with paper + citations + references."""
    return _json_response(PAPER_PAYLOAD_WITH_CITATIONS_AND_REFERENCES)


@pytest.mark.asyncio
async def test_citation_graph_success():
    """Citation graph should return citations and references with normalized fields from ONE API call."""
    mock_client = _mock_async_client([_success_response()])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph({"paper_id": "2401.12345"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["citation_count"] == 1
    assert payload["reference_count"] == 1
    assert payload["citation_total"] == 12
    assert payload["max_citations"] == 50
    assert payload["citations"][0]["arxiv_id"] == "2501.00001"
    # Should only make 1 API call now (not 3)
    assert mock_client.get.call_count == 1
    # Verify the single call includes citations and references fields
    call_args = mock_client.get.call_args
    assert "citations" in call_args.kwargs["params"]["fields"]
    assert "references" in call_args.kwargs["params"]["fields"]
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
    """A transient Semantic Scholar 429 should be retried and then succeed with ONE final API call."""
    responses = [_json_response({}, status_code=429), _success_response()]
    mock_client = _mock_async_client(responses)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(
            citation_graph_module.asyncio, "sleep", new_callable=AsyncMock
        ) as sleep,
        patch.object(citation_graph_module.random, "random", return_value=0.5),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph({"paper_id": "1706.03762"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["paper"]["arxiv_id"] == "1706.03762"
    # First call gets 429, second call succeeds
    assert mock_client.get.call_count == 2
    sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_citation_graph_429_exhausted_returns_soft_rate_limited():
    """Persistent 429s should soft-fail with unmistakable API-key guidance."""
    attempts = citation_graph_module._MAX_RETRIES + 1
    rate_limited = _json_response({}, status_code=429)
    mock_client = _mock_async_client([rate_limited] * attempts)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module.asyncio, "sleep", new_callable=AsyncMock),
        patch.object(citation_graph_module.random, "random", return_value=0.5),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph(
            {"paper_id": "2608.18261", "max_citations": 10}
        )

    payload = json.loads(response[0].text)
    assert payload["status"] == "rate_limited"
    assert payload["error"] == "RATE_LIMITED"
    assert payload["arxiv_id"] == "2608.18261"
    assert payload["max_citations"] == 10
    assert payload["citations"] == []
    assert payload["references"] == []
    assert payload["citation_count"] == 0
    assert payload["reference_count"] == 0
    assert "SEMANTIC_SCHOLAR_API_KEY" in payload["message"]
    assert "NOT an empty" in payload["warning"]
    assert "free API key" in payload["hint"]
    assert not response[0].text.startswith("Error:")
    assert mock_client.get.call_count == attempts


@pytest.mark.asyncio
async def test_citation_graph_429_exhausted_with_api_key_guidance():
    """When an API key is set, rate-limit guidance should mention that fact."""
    attempts = citation_graph_module._MAX_RETRIES + 1
    rate_limited = _json_response({}, status_code=429)
    mock_client = _mock_async_client([rate_limited] * attempts)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module.asyncio, "sleep", new_callable=AsyncMock),
        patch.object(
            citation_graph_module.settings, "SEMANTIC_SCHOLAR_API_KEY", "s2-key"
        ),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph({"paper_id": "2410.17954"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "rate_limited"
    assert payload["error"] == "RATE_LIMITED"
    assert "despite SEMANTIC_SCHOLAR_API_KEY" in payload["message"]
    assert "NOT an empty" in payload["warning"]
    assert "hint" not in payload


def test_backoff_seconds_longer_with_jitter():
    """Backoff should start higher than the old 1s ladder and include jitter."""
    with patch.object(citation_graph_module.random, "random", return_value=0.5):
        # 0.5 jitter multiplier => delay * 1.0 (0.5 + 0.5)
        assert citation_graph_module._backoff_seconds(0, None) == 2.0
        assert citation_graph_module._backoff_seconds(1, None) == 4.0
        assert citation_graph_module._backoff_seconds(2, None) == 8.0
        assert citation_graph_module._backoff_seconds(3, None) == 16.0
        assert citation_graph_module._backoff_seconds(4, None) == 32.0
        assert citation_graph_module._backoff_seconds(5, None) == 60.0

    with patch.object(citation_graph_module.random, "random", return_value=0.0):
        # Minimum jitter is 50% of the exponential delay.
        assert citation_graph_module._backoff_seconds(0, None) == 1.0
        assert citation_graph_module._backoff_seconds(1, None) == 2.0

    with patch.object(citation_graph_module.random, "random", return_value=1.0):
        # Retry-After can raise the floor before jitter, still capped.
        assert citation_graph_module._backoff_seconds(0, "45") == 60.0


@pytest.mark.asyncio
async def test_citation_graph_sends_api_key_and_honors_max_citations():
    """Optional API key and max_citations should be forwarded to Semantic Scholar."""
    mock_client = _mock_async_client([_success_response()])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(
            citation_graph_module.settings, "SEMANTIC_SCHOLAR_API_KEY", "s2-key"
        ),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph(
            {"paper_id": "2401.12345", "max_citations": 10}
        )

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["max_citations"] == 10
    for call in mock_client.get.call_args_list:
        assert call.kwargs["headers"]["x-api-key"] == "s2-key"


@pytest.mark.asyncio
async def test_citation_graph_neighbors_include_arxiv_id_from_external_ids():
    """Neighbors with ArXiv in externalIds must surface arxiv_id for tool hops."""
    payload_with_neighbors = {
        "paperId": "root-paper",
        "title": "Root Paper",
        "year": 2024,
        "authors": [{"name": "Author A"}],
        "externalIds": {"ArXiv": "2505.16056"},
        "citationCount": 2,
        "referenceCount": 2,
        "citations": [
            {
                "paperId": "sticky-routing",
                "title": "Sticky Routing",
                "year": 2026,
                "authors": [{"name": "Author S"}],
                "externalIds": {"ArXiv": "2607.08780"},
            }
        ],
        "references": [
            {
                "paperId": "promoe",
                "title": "ProMoE",
                "year": 2024,
                "authors": [{"name": "Author P"}],
                "externalIds": {"ArXiv": "2410.22134"},
            }
        ],
    }
    mock_client = _mock_async_client([_json_response(payload_with_neighbors)])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph({"paper_id": "2505.16056"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["citations"][0]["arxiv_id"] == "2607.08780"
    assert payload["references"][0]["arxiv_id"] == "2410.22134"
    call_fields = mock_client.get.call_args.kwargs["params"]["fields"]
    assert "citations" in call_fields
    assert "references" in call_fields
    assert "externalIds" in call_fields


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paper_id,expected_version",
    [("1706.03762v1", "v1"), ("1706.03762v7", "v7")],
)
async def test_citation_graph_strips_version_for_s2_lookup(paper_id, expected_version):
    """Versioned arXiv IDs must query Semantic Scholar with the bare ARXIV id."""
    paper_payload = {
        **PAPER_PAYLOAD_WITH_CITATIONS_AND_REFERENCES,
        "externalIds": {"ArXiv": "1706.03762"},
    }
    mock_client = _mock_async_client([_json_response(paper_payload)])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph({"paper_id": paper_id})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["paper"]["arxiv_id"] == "1706.03762"
    assert payload["paper"]["requested_arxiv_id"] == paper_id
    assert payload["paper"]["requested_version"] == expected_version

    paper_url = mock_client.get.call_args.args[0]
    assert paper_url.endswith("/paper/ARXIV:1706.03762")
    assert expected_version not in paper_url.split("/paper/", 1)[1]


@pytest.mark.asyncio
async def test_citation_graph_bare_id_omits_requested_version_fields():
    """Bare IDs should not invent requested_version metadata."""
    mock_client = _mock_async_client([_success_response()])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph({"paper_id": "2401.12345"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["paper"]["arxiv_id"] == "2401.12345"
    assert "requested_arxiv_id" not in payload["paper"]
    assert "requested_version" not in payload["paper"]
    assert mock_client.get.call_args.args[0].endswith("/paper/ARXIV:2401.12345")


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

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        mock_cache_dir.return_value = Path("/tmp/nonexistent_cache")
        response = await handle_citation_graph({"paper_id": "1706.03762v1"})

    assert response[0].text == "Error: paper not found on Semantic Scholar"
    assert "semanticscholar.org" not in response[0].text
    assert "https://" not in response[0].text


@pytest.mark.asyncio
async def test_citation_graph_cache_hit():
    """Second call for same paper should return cached result without API call."""
    mock_client = _mock_async_client([_success_response()])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        cache_dir = Path("/tmp/test_citation_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        mock_cache_dir.return_value = cache_dir

        # First call: cache miss, API call made
        response1 = await handle_citation_graph({"paper_id": "2401.12345"})
        payload1 = json.loads(response1[0].text)
        assert payload1["status"] == "success"
        assert mock_client.get.call_count == 1

        # Second call: cache hit, no API call
        response2 = await handle_citation_graph({"paper_id": "2401.12345"})
        payload2 = json.loads(response2[0].text)
        assert payload2["status"] == "success"
        assert mock_client.get.call_count == 1  # Still 1, no new call
        assert payload2["paper"]["arxiv_id"] == "2401.12345"

        # Clean up
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_citation_graph_cache_respects_max_citations():
    """Cached result with smaller limit should not satisfy larger limit request."""
    mock_client = _mock_async_client([_success_response(), _success_response()])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        cache_dir = Path("/tmp/test_citation_cache_limits")
        cache_dir.mkdir(parents=True, exist_ok=True)
        mock_cache_dir.return_value = cache_dir

        # First call with max_citations=10
        response1 = await handle_citation_graph(
            {"paper_id": "2401.12345", "max_citations": 10}
        )
        payload1 = json.loads(response1[0].text)
        assert payload1["status"] == "success"
        assert payload1["max_citations"] == 10
        assert mock_client.get.call_count == 1

        # Second call with max_citations=50 should NOT use cache
        response2 = await handle_citation_graph(
            {"paper_id": "2401.12345", "max_citations": 50}
        )
        payload2 = json.loads(response2[0].text)
        assert payload2["status"] == "success"
        assert payload2["max_citations"] == 50
        assert mock_client.get.call_count == 2  # New API call made

        # Clean up
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_citation_graph_cache_rate_limited_expires_quickly():
    """Rate-limited results should expire quickly from cache."""
    attempts = citation_graph_module._MAX_RETRIES + 1
    rate_limited = _json_response({}, status_code=429)
    mock_client = _mock_async_client([rate_limited] * attempts + [_success_response()])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module.asyncio, "sleep", new_callable=AsyncMock),
        patch.object(citation_graph_module.random, "random", return_value=0.5),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
        patch.object(citation_graph_module, "CACHE_TTL_RATE_LIMITED_SECONDS", 0.1),
    ):
        cache_dir = Path("/tmp/test_citation_cache_rate_limited")
        cache_dir.mkdir(parents=True, exist_ok=True)
        mock_cache_dir.return_value = cache_dir

        # First call: rate limited
        response1 = await handle_citation_graph({"paper_id": "2401.12345"})
        payload1 = json.loads(response1[0].text)
        assert payload1["status"] == "rate_limited"
        assert payload1["error"] == "RATE_LIMITED"
        assert mock_client.get.call_count == attempts

        # Wait for cache to expire
        time.sleep(0.2)

        # Second call: cache expired, new API call succeeds
        response2 = await handle_citation_graph({"paper_id": "2401.12345"})
        payload2 = json.loads(response2[0].text)
        assert payload2["status"] == "success"
        assert mock_client.get.call_count == attempts + 1  # New call made

        # Clean up
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_citation_graph_cache_can_serve_smaller_limit():
    """Cached result with larger limit can satisfy smaller limit request."""
    import shutil

    mock_client = _mock_async_client([_success_response()])

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(citation_graph_module, "_cache_dir") as mock_cache_dir,
    ):
        cache_dir = Path("/tmp/test_citation_cache_slice")
        # Clean up any previous cache
        shutil.rmtree(cache_dir, ignore_errors=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        mock_cache_dir.return_value = cache_dir

        # First call with max_citations=50
        response1 = await handle_citation_graph(
            {"paper_id": "2401.12345", "max_citations": 50}
        )
        payload1 = json.loads(response1[0].text)
        assert payload1["status"] == "success"
        assert payload1["max_citations"] == 50
        assert mock_client.get.call_count == 1

        # Second call with max_citations=10 should use cache
        response2 = await handle_citation_graph(
            {"paper_id": "2401.12345", "max_citations": 10}
        )
        payload2 = json.loads(response2[0].text)
        assert payload2["status"] == "success"
        assert payload2["max_citations"] == 10
        assert mock_client.get.call_count == 1  # No new call

        # Clean up
        shutil.rmtree(cache_dir, ignore_errors=True)
