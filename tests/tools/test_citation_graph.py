"""Tests for citation graph tool."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arxiv_mcp_server.tools import citation_graph
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

    # Golden byte-for-byte: pin the EXACT default output so a future change to
    # the legacy path cannot silently alter it (backward-compat guarantee).
    expected = {
        "status": "success",
        "paper": {
            "paper_id": "root-paper",
            "arxiv_id": "2401.12345",
            "title": "Root Paper",
            "year": 2024,
            "authors": ["Author A"],
            "external_ids": {"ArXiv": "2401.12345"},
        },
        "citation_count": 1,
        "reference_count": 1,
        "citations": [
            {
                "paper_id": "citing-1",
                "title": "Citing Paper",
                "year": 2025,
                "authors": ["Author B"],
                "external_ids": {"ArXiv": "2501.00001"},
                "arxiv_id": "2501.00001",
            }
        ],
        "references": [
            {
                "paper_id": "ref-1",
                "title": "Referenced Paper",
                "year": 2020,
                "authors": ["Author C"],
                "external_ids": {"ArXiv": "2001.00001"},
                "arxiv_id": "2001.00001",
            }
        ],
    }
    assert text == json.dumps(expected, indent=2)


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


@pytest.mark.asyncio
async def test_citation_graph_offset_only_uses_legacy():
    """`offset` alone must NOT trigger pagination (backward-compat trap, FIX A).

    Asserts the legacy path: exactly ONE client.get await, indent=2 output
    (newline present), and no `pagination` key.
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

        response = await handle_citation_graph({"paper_id": "2401.12345", "offset": 5})

    text = response[0].text
    # Legacy path makes exactly one (nested) request.
    assert mock_client.get.await_count == 1
    # Legacy path uses indent=2 -> newlines present in the rendered JSON.
    assert "\n" in text

    payload = json.loads(text)
    assert "pagination" not in payload


@pytest.mark.asyncio
async def test_citation_graph_compact_default_limit():
    """`compact` with no `limit` must default the page limit to 100 (FIX A path)."""
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
    citations_response.json.return_value = {"offset": 0, "next": 100, "data": []}

    references_response = MagicMock()
    references_response.raise_for_status = MagicMock()
    references_response.json.return_value = {"offset": 0, "data": []}

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[root_response, citations_response, references_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph(
            {"paper_id": "2401.12345", "compact": True}
        )

    payload = json.loads(response[0].text)
    assert payload["pagination"]["limit"] == 100


@pytest.mark.asyncio
async def test_citation_graph_paginated_http_error():
    """Paginated path surfaces HTTP errors with no partial result (FIX D)."""
    root_response = MagicMock()
    root_response.raise_for_status = MagicMock()
    root_response.json.return_value = {
        "paperId": "root-paper",
        "title": "Root Paper",
        "year": 2024,
        "authors": [{"name": "Author A"}],
        "externalIds": {"ArXiv": "2401.12345"},
    }

    # The /citations response raises on raise_for_status.
    failing_response = MagicMock()
    failing_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=MagicMock()
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[root_response, failing_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345", "limit": 5})

    text = response[0].text
    assert text.startswith("Error:")
    # No partial result emitted.
    assert "pagination" not in text


@pytest.mark.asyncio
async def test_citation_graph_paginated_empty_data():
    """Empty /citations data: count 0, no crash, `next` None when absent (FIX D)."""
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
    # No `next` key -> should normalize to None.
    citations_response.json.return_value = {"offset": 0, "data": []}

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

        response = await handle_citation_graph({"paper_id": "2401.12345", "limit": 5})

    payload = json.loads(response[0].text)
    assert payload["citation_count"] == 0
    assert payload["citations"] == []
    assert payload["pagination"]["citations"]["next"] is None


@pytest.mark.asyncio
async def test_citation_graph_limit_offset_clamped():
    """Out-of-range limit/offset are clamped in code (FIX B).

    limit=99999 -> 1000, offset=-5 -> 0, reflected in the request URLs.
    """
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
    citations_response.json.return_value = {"offset": 0, "data": []}

    references_response = MagicMock()
    references_response.raise_for_status = MagicMock()
    references_response.json.return_value = {"offset": 0, "data": []}

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[root_response, citations_response, references_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await handle_citation_graph(
            {
                "paper_id": "2401.12345",
                "limit": 99999,
                "offset": -5,
                "compact": True,
            }
        )

    # Inspect the awaited request URLs (positional arg 0 of each client.get call).
    awaited_urls = [call.args[0] for call in mock_client.get.call_args_list]
    # The two paged endpoints (/citations, /references) must carry the clamped
    # values. The root metadata request carries neither.
    paged_urls = [u for u in awaited_urls if "limit=" in u]
    assert paged_urls, "expected paged endpoint URLs with limit/offset"
    for url in paged_urls:
        assert "limit=1000" in url
        assert "offset=0" in url


def _paginated_mocks(citations_next):
    """Build (root, citations, references) response mocks for the paginated path."""
    root = MagicMock()
    root.raise_for_status = MagicMock()
    root.json.return_value = {
        "paperId": "root-paper",
        "title": "Root Paper",
        "year": 2024,
        "authors": [{"name": "Author A"}],
        "externalIds": {"ArXiv": "2401.12345"},
    }
    citations = MagicMock()
    citations.raise_for_status = MagicMock()
    citations.json.return_value = {
        "offset": 0,
        "next": citations_next,
        "data": [{"citingPaper": {"paperId": "c1", "title": "C", "year": 2025}}],
    }
    references = MagicMock()
    references.raise_for_status = MagicMock()
    references.json.return_value = {"offset": 0, "data": []}
    return root, citations, references


@pytest.mark.asyncio
async def test_citation_graph_pagination_next_offset_roundtrip():
    """The `next` cursor from page 1 is usable as the `offset` for page 2.

    Pins the README's documented paging loop: read pagination.citations.next,
    feed it back as `offset`, and the next request URL carries that offset.
    """
    # Page 1: limit=5, offset 0 -> citations.next == 5.
    root1, cit1, ref1 = _paginated_mocks(citations_next=5)
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[root1, cit1, ref1])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        page1 = await handle_citation_graph({"paper_id": "2401.12345", "limit": 5})

    next_cursor = json.loads(page1[0].text)["pagination"]["citations"]["next"]
    assert next_cursor == 5

    # Page 2: feed next_cursor back as offset; the citations URL must carry it.
    root2, cit2, ref2 = _paginated_mocks(citations_next=None)
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[root2, cit2, ref2])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await handle_citation_graph(
            {"paper_id": "2401.12345", "limit": 5, "offset": next_cursor}
        )

    citations_urls = [
        c.args[0] for c in mock_client.get.call_args_list if "/citations" in c.args[0]
    ]
    assert citations_urls and f"offset={next_cursor}" in citations_urls[0]


@pytest.mark.asyncio
async def test_citation_graph_old_style_id_quoted():
    """Old-style arXiv IDs contain a slash (e.g. hep-th/9901001); it must be
    percent-encoded so it is not treated as a URL path separator."""
    root, cit, ref = _paginated_mocks(citations_next=None)
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[root, cit, ref])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await handle_citation_graph({"paper_id": "hep-th/9901001", "limit": 5})

    urls = [c.args[0] for c in mock_client.get.call_args_list]
    assert urls, "expected requests to be made"
    for u in urls:
        # The id's slash is encoded (%2F); the raw `hep-th/9901001` never appears.
        assert "hep-th%2F9901001" in u
        assert "hep-th/9901001" not in u


@pytest.mark.asyncio
async def test_citation_graph_compact_strict_bool():
    """A non-bool truthy `compact` (e.g. the string "false") must NOT enable the
    compact/paginated path — only a real JSON true does (defense-in-depth)."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _legacy_mock_payload()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph(
            {"paper_id": "2401.12345", "compact": "false"}
        )

    # Legacy path: exactly one request, no pagination block.
    assert mock_client.get.await_count == 1
    assert "pagination" not in json.loads(response[0].text)


@pytest.mark.asyncio
async def test_citation_graph_retries_on_429():
    """A transient 429 is retried; the subsequent 200 succeeds (FIX C2)."""
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {}

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.headers = {}
    ok_response.raise_for_status = MagicMock()
    ok_response.json.return_value = _legacy_mock_payload()

    with (
        patch("httpx.AsyncClient") as mock_client_class,
        patch("arxiv_mcp_server.tools.citation_graph.asyncio.sleep", new=AsyncMock()),
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_limited, ok_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    # First call hit 429, second call returned the 200 payload.
    assert mock_client.get.await_count == 2


@pytest.mark.asyncio
async def test_citation_graph_429_exhausted():
    """A 429 that survives all retries surfaces as an Error envelope (FIX C2).

    max_retries defaults to 4 -> 1 initial + 4 retries == 5 awaited GETs.
    """
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {}
    rate_limited.raise_for_status.side_effect = httpx.HTTPStatusError(
        "rate limited", request=MagicMock(), response=MagicMock()
    )

    with (
        patch("httpx.AsyncClient") as mock_client_class,
        patch("arxiv_mcp_server.tools.citation_graph.asyncio.sleep", new=AsyncMock()),
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=rate_limited)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    assert response[0].text.startswith("Error:")
    # 1 initial GET + max_retries (4) retries == 5.
    assert mock_client.get.await_count == 5


@pytest.mark.asyncio
async def test_citation_graph_retry_after_header():
    """A numeric Retry-After header drives the backoff delay (FIX C2)."""
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {"Retry-After": "7"}

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.headers = {}
    ok_response.raise_for_status = MagicMock()
    ok_response.json.return_value = _legacy_mock_payload()

    sleep_mock = AsyncMock()
    with (
        patch("httpx.AsyncClient") as mock_client_class,
        patch("arxiv_mcp_server.tools.citation_graph.asyncio.sleep", new=sleep_mock),
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_limited, ok_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await handle_citation_graph({"paper_id": "2401.12345"})

    sleep_mock.assert_awaited_once_with(7.0)


@pytest.mark.asyncio
async def test_citation_graph_output_cap(monkeypatch):
    """An output cap truncates each direction and flags `truncated` (FIX C2)."""
    monkeypatch.setattr(citation_graph.settings, "CITATION_MAX_EDGES", 1)

    payload = _legacy_mock_payload()
    payload["citations"].append(
        {
            "paperId": "citing-2",
            "title": "Citing Paper 2",
            "year": 2025,
            "authors": [{"name": "Author D"}],
            "externalIds": {"ArXiv": "2501.00002"},
        }
    )
    payload["references"].append(
        {
            "paperId": "ref-2",
            "title": "Referenced Paper 2",
            "year": 2019,
            "authors": [{"name": "Author E"}],
            "externalIds": {"ArXiv": "2001.00002"},
        }
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = payload

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    result = json.loads(response[0].text)
    assert result["citation_count"] == 1
    assert result["reference_count"] == 1
    assert result["truncated"] is True
    assert len(result["citations"]) == 1
    assert len(result["references"]) == 1


@pytest.mark.asyncio
async def test_citation_graph_cap_unset_no_key():
    """With the default cap (None), no `truncated` key appears (golden contract)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _legacy_mock_payload()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    assert "truncated" not in json.loads(response[0].text)


@pytest.mark.asyncio
async def test_citation_graph_retries_on_5xx():
    """A transient 503 is retried; the subsequent 200 legacy payload succeeds."""
    server_error = MagicMock()
    server_error.status_code = 503
    server_error.headers = {}

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.headers = {}
    ok_response.raise_for_status = MagicMock()
    ok_response.json.return_value = _legacy_mock_payload()

    with (
        patch("httpx.AsyncClient") as mock_client_class,
        patch("arxiv_mcp_server.tools.citation_graph.asyncio.sleep", new=AsyncMock()),
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[server_error, ok_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert mock_client.get.await_count == 2


@pytest.mark.asyncio
async def test_citation_graph_retries_on_transport_error():
    """A transport error (ConnectError) is retried; the next 200 succeeds."""
    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.headers = {}
    ok_response.raise_for_status = MagicMock()
    ok_response.json.return_value = _legacy_mock_payload()

    with (
        patch("httpx.AsyncClient") as mock_client_class,
        patch("arxiv_mcp_server.tools.citation_graph.asyncio.sleep", new=AsyncMock()),
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[httpx.ConnectError("boom"), ok_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert mock_client.get.await_count == 2


@pytest.mark.asyncio
async def test_citation_graph_retry_after_clamped():
    """An absurd Retry-After is clamped to MAX_RETRY_DELAY, not slept literally."""
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {"Retry-After": "99999"}

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.headers = {}
    ok_response.raise_for_status = MagicMock()
    ok_response.json.return_value = _legacy_mock_payload()

    sleep_mock = AsyncMock()
    with (
        patch("httpx.AsyncClient") as mock_client_class,
        patch("arxiv_mcp_server.tools.citation_graph.asyncio.sleep", new=sleep_mock),
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_limited, ok_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await handle_citation_graph({"paper_id": "2401.12345"})

    # Clamped to MAX_RETRY_DELAY (16.0), NOT the literal 99999.
    sleep_mock.assert_awaited_once_with(16.0)


@pytest.mark.asyncio
async def test_citation_graph_backoff_jitter():
    """With no Retry-After, the backoff uses jittered random.uniform."""
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {}

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.headers = {}
    ok_response.raise_for_status = MagicMock()
    ok_response.json.return_value = _legacy_mock_payload()

    sleep_mock = AsyncMock()
    with (
        patch("httpx.AsyncClient") as mock_client_class,
        patch("arxiv_mcp_server.tools.citation_graph.asyncio.sleep", new=sleep_mock),
        patch(
            "arxiv_mcp_server.tools.citation_graph.random.uniform", return_value=0.5
        ) as uniform_mock,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_limited, ok_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await handle_citation_graph({"paper_id": "2401.12345"})

    uniform_mock.assert_called()
    sleep_mock.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_citation_graph_paginated_retries():
    """The paginated path retries a 429 on a sub-request and still succeeds."""
    root_response = MagicMock()
    root_response.status_code = 200
    root_response.headers = {}
    root_response.raise_for_status = MagicMock()
    root_response.json.return_value = {
        "paperId": "root-paper",
        "title": "Root Paper",
        "year": 2024,
        "authors": [{"name": "Author A"}],
        "externalIds": {"ArXiv": "2401.12345"},
    }

    citations_429 = MagicMock()
    citations_429.status_code = 429
    citations_429.headers = {}

    citations_ok = MagicMock()
    citations_ok.status_code = 200
    citations_ok.headers = {}
    citations_ok.raise_for_status = MagicMock()
    citations_ok.json.return_value = {
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
    references_response.status_code = 200
    references_response.headers = {}
    references_response.raise_for_status = MagicMock()
    references_response.json.return_value = {"offset": 0, "data": []}

    with (
        patch("httpx.AsyncClient") as mock_client_class,
        patch("arxiv_mcp_server.tools.citation_graph.asyncio.sleep", new=AsyncMock()),
    ):
        mock_client = AsyncMock()
        # root, citations(429), citations(200 retry), references.
        mock_client.get = AsyncMock(
            side_effect=[
                root_response,
                citations_429,
                citations_ok,
                references_response,
            ]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345", "limit": 5})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert "pagination" in payload
    assert payload["citation_count"] == 1


@pytest.mark.asyncio
async def test_citation_graph_paginated_no_cap(monkeypatch):
    """The output cap must NOT apply in the paginated path (cursor integrity)."""
    monkeypatch.setattr(citation_graph.settings, "CITATION_MAX_EDGES", 1)

    root_response = MagicMock()
    root_response.status_code = 200
    root_response.headers = {}
    root_response.raise_for_status = MagicMock()
    root_response.json.return_value = {
        "paperId": "root-paper",
        "title": "Root Paper",
        "year": 2024,
        "authors": [{"name": "Author A"}],
        "externalIds": {"ArXiv": "2401.12345"},
    }

    citations_response = MagicMock()
    citations_response.status_code = 200
    citations_response.headers = {}
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
            },
            {
                "citingPaper": {
                    "paperId": "citing-2",
                    "title": "Citing Paper 2",
                    "year": 2025,
                    "authors": [{"name": "Author D"}],
                    "externalIds": {"ArXiv": "2501.00002"},
                }
            },
        ],
    }

    references_response = MagicMock()
    references_response.status_code = 200
    references_response.headers = {}
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
            },
            {
                "citedPaper": {
                    "paperId": "ref-2",
                    "title": "Referenced Paper 2",
                    "year": 2019,
                    "authors": [{"name": "Author E"}],
                    "externalIds": {"ArXiv": "2001.00002"},
                }
            },
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

        response = await handle_citation_graph({"paper_id": "2401.12345", "limit": 5})

    payload = json.loads(response[0].text)
    # Cap (1) is NOT applied in the paginated path: full page returned, no flag.
    assert "truncated" not in payload
    assert payload["citation_count"] == 2
    assert payload["reference_count"] == 2
    assert len(payload["citations"]) == 2
    assert len(payload["references"]) == 2


@pytest.mark.asyncio
async def test_citation_graph_negative_cap_ignored(monkeypatch):
    """A negative cap is treated as "no cap" (no negative-slice truncation)."""
    monkeypatch.setattr(citation_graph.settings, "CITATION_MAX_EDGES", -1)

    payload = _legacy_mock_payload()
    payload["citations"].append(
        {
            "paperId": "citing-2",
            "title": "Citing Paper 2",
            "year": 2025,
            "authors": [{"name": "Author D"}],
            "externalIds": {"ArXiv": "2501.00002"},
        }
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = payload

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        response = await handle_citation_graph({"paper_id": "2401.12345"})

    result = json.loads(response[0].text)
    # Negative cap == no cap: both citations returned, no truncation flag.
    assert "truncated" not in result
    assert result["citation_count"] == 2
    assert len(result["citations"]) == 2
