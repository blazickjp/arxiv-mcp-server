"""Tests for paper search functionality."""

import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from arxiv_mcp_server.tools import handle_search
from arxiv_mcp_server.tools import search as search_module
from arxiv_mcp_server.tools.search import (
    DEFAULT_ABSTRACT_MODE,
    DEFAULT_MAX_RESULTS,
    ABSTRACT_SNIPPET_CHARS,
    SORT_BY_VALUES,
    _MAX_RETRIES,
    _validate_categories,
    _raw_arxiv_search,
    _rate_limited_get,
    _parse_arxiv_atom_response,
    _parse_opensearch_total_results,
    _build_search_response,
    _apply_abstract_mode,
    _snippet_abstract,
    _normalize_abstract_mode,
    _normalize_sort_by,
    _scope_user_query,
    _backoff_seconds,
    build_arxiv_search_query,
    build_arxiv_search_url,
)


@pytest.fixture(autouse=True)
def disable_request_spacing_for_search_unit_tests(monkeypatch):
    """Keep search tests fast and isolate the process-wide client."""
    from arxiv_mcp_server import config

    monkeypatch.setattr(search_module.ARXIV_RATE_LIMITER, "min_interval", 0.0)
    monkeypatch.setattr(search_module.ARXIV_RATE_LIMITER, "_last_started", 0.0)
    monkeypatch.setattr(config, "_arxiv_client", None)


def _mock_httpx_response(xml_text: str, *, status_code: int = 200, headers=None):
    """Patch httpx.AsyncClient to return an Atom feed body."""
    mock_response = MagicMock()
    mock_response.text = xml_text
    mock_response.status_code = status_code
    mock_response.headers = headers or {}
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_response, mock_client


@pytest.mark.asyncio
async def test_basic_search():
    """Test basic paper search includes OpenSearch total_results (#189)."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=1)
    # Use a fixed id matching historical fixture expectations where possible.
    xml = xml.replace("2301.00001", "2103.12345").replace("Test Paper 1", "Test Paper")
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search({"query": "test query", "max_results": 1})

        assert len(result) == 1
        content = json.loads(result[0].text)
        assert content["returned"] == 1
        assert content["start"] == 0
        assert content["total_results"] == 1
        assert content["has_more"] is False
        assert content["next_start"] is None
        paper = content["papers"][0]
        assert paper["id"] == "2103.12345"
        assert paper["title"] == "Test Paper"
        assert "resource_uri" in paper


@pytest.mark.asyncio
async def test_search_uses_process_wide_rate_limiter(mocker):
    """All search_papers requests share the process-wide arXiv gate."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=1)
    _, mock_client = _mock_httpx_response(xml)

    async def run_async(operation):
        return await operation()

    mocked = mocker.patch.object(
        search_module.ARXIV_RATE_LIMITER,
        "run_async",
        side_effect=run_async,
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        await handle_search({"query": "test query", "max_results": 1})

    mocked.assert_called_once()


@pytest.mark.asyncio
async def test_search_with_categories():
    """Test paper search with category filtering via raw Atom API."""
    xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\" xmlns:opensearch=\"http://a9.com/-/spec/opensearch/1.1/\">
        <opensearch:totalResults>1</opensearch:totalResults>
        <entry>
            <id>http://arxiv.org/abs/2103.12345v1</id>
            <title>Test Paper</title>
            <summary>Test abstract</summary>
            <published>2023-01-01T00:00:00Z</published>
            <author><name>John Doe</name></author>
            <arxiv:primary_category term=\"cs.AI\"/>
            <category term=\"cs.AI\"/>
            <category term=\"cs.LG\"/>
            <link title=\"pdf\" href=\"http://arxiv.org/pdf/2103.12345v1\"/>
        </entry>
    </feed>"""
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {"query": "test query", "categories": ["cs.AI", "cs.LG"], "max_results": 1}
        )

        content = json.loads(result[0].text)
        assert content["total_results"] == 1
        assert content["papers"][0]["categories"] == ["cs.AI", "cs.LG"]


@pytest.mark.asyncio
async def test_search_with_dates():
    """Test paper search with date filtering uses raw API."""
    mock_xml_response = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\" xmlns:opensearch=\"http://a9.com/-/spec/opensearch/1.1/\">
        <opensearch:totalResults>42</opensearch:totalResults>
        <entry>
            <id>http://arxiv.org/abs/2301.00001v1</id>
            <title>Test Paper</title>
            <summary>Test abstract</summary>
            <published>2023-06-15T00:00:00Z</published>
            <author><name>Test Author</name></author>
            <arxiv:primary_category term=\"cs.AI\"/>
            <link title=\"pdf\" href=\"http://arxiv.org/pdf/2301.00001v1\"/>
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
        assert content["total_results"] == 42
        assert content["returned"] == 1
        assert content["start"] == 0
        assert content["has_more"] is True
        assert content["next_start"] == 1
        assert len(content["papers"]) == 1


@pytest.mark.asyncio
async def test_search_with_invalid_dates():
    """Test search with invalid date formats."""
    result = await handle_search(
        {"query": "test query", "date_from": "invalid-date", "max_results": 1}
    )

    content = json.loads(result[0].text)
    assert content["status"] == "error"
    assert "Invalid date format" in content["message"]
    assert not result[0].text.startswith("Error:")


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
    sample_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\">
        <entry>
            <id>http://arxiv.org/abs/2301.00001v1</id>
            <title>Test Paper Title</title>
            <summary>This is a test abstract.</summary>
            <published>2023-01-01T00:00:00Z</published>
            <author><name>John Doe</name></author>
            <author><name>Jane Smith</name></author>
            <arxiv:primary_category term=\"cs.AI\"/>
            <category term=\"cs.AI\"/>
            <category term=\"cs.LG\"/>
            <link title=\"pdf\" href=\"http://arxiv.org/pdf/2301.00001v1\"/>
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
    mock_response.text = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\">
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

        # Date ranges must decode to " TO ", never %2BTO%2B
        from urllib.parse import unquote

        call_args = mock_client.get.call_args
        url = call_args[0][0]
        decoded = unquote(url)
        assert "%2BTO%2B" not in url
        assert " TO " in decoded
        assert "submittedDate:" in decoded
        assert "20230101" in url
        assert "20231231" in url


@pytest.mark.asyncio
async def test_search_with_invalid_categories():
    """Test search with invalid categories."""
    result = await handle_search(
        {
            "query": "test query",
            "categories": ["invalid.category"],
            "max_results": 1,
        }
    )

    content = json.loads(result[0].text)
    assert content["status"] == "error"
    assert "Invalid category" in content["message"]
    assert not result[0].text.startswith("Error:")


@pytest.mark.asyncio
async def test_search_empty_query():
    """Test search with empty query but categories."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=7)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {"query": "", "categories": ["cs.AI"], "max_results": 1}
        )

        content = json.loads(result[0].text)
        assert "papers" in content
        assert content["total_results"] == 7


@pytest.mark.asyncio
async def test_search_arxiv_http_error():
    """Test handling of arXiv HTTP API errors."""
    import httpx

    request = httpx.Request("GET", "https://export.arxiv.org/api/query")
    response = httpx.Response(500, request=request)
    error = httpx.HTTPStatusError("boom", request=request, response=response)

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.headers = {}
    mock_response.raise_for_status.side_effect = error

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search({"query": "test", "max_results": 1})

    content = json.loads(result[0].text)
    assert content["status"] == "error"
    assert "arXiv API HTTP error" in content["message"]
    assert "HTTP 500" in content["message"]
    assert "export.arxiv.org" not in result[0].text
    assert not result[0].text.startswith("Error:")


@pytest.mark.asyncio
async def test_search_max_results_limiting():
    """Test that max_results is properly limited."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=50)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
        result = await handle_search({"query": "test", "max_results": 1000})

        content = json.loads(result[0].text)
        assert "papers" in content
        assert content["total_results"] == 50
        from urllib.parse import parse_qs, urlparse

        url = mock_client.get.call_args[0][0]
        params = parse_qs(urlparse(url).query)
        # Cap is settings.MAX_RESULTS (50)
        assert int(params["max_results"][0]) <= 50


@pytest.mark.asyncio
async def test_search_forwards_varying_max_results_to_arxiv_api():
    """Varying max_results must be forwarded on the raw Atom query URL."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=100)
    _, mock_client = _mock_httpx_response(xml)
    observed = []

    async def capture_get(url, **kwargs):
        from urllib.parse import parse_qs, urlparse

        observed.append(int(parse_qs(urlparse(url).query)["max_results"][0]))
        resp = MagicMock()
        resp.text = xml
        resp.raise_for_status = MagicMock()
        return resp

    mock_client.get = AsyncMock(side_effect=capture_get)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await handle_search({"query": "first", "max_results": 5})
        await handle_search({"query": "second", "max_results": 7})

    assert observed == [5, 7]


@pytest.mark.asyncio
async def test_search_sort_by_relevance():
    """Test search with relevance sorting (default)."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=3)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search({"query": "test", "sort_by": "relevance"})

        content = json.loads(result[0].text)
        assert "papers" in content
        assert content["total_results"] == 3
        from urllib.parse import parse_qs, urlparse

        url = mock_client.get.call_args[0][0]
        assert parse_qs(urlparse(url).query)["sortBy"] == ["relevance"]


@pytest.mark.asyncio
async def test_search_sort_by_date():
    """Test search with date sorting."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=3)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search({"query": "test", "sort_by": "date"})

        content = json.loads(result[0].text)
        assert "papers" in content
        assert content["total_results"] == 3
        from urllib.parse import parse_qs, urlparse

        url = mock_client.get.call_args[0][0]
        assert parse_qs(urlparse(url).query)["sortBy"] == ["submittedDate"]


def test_normalize_sort_by_defaults_and_rejects_invalid():
    """Only documented sort_by values are accepted (#242)."""
    assert _normalize_sort_by(None) == "relevance"
    assert _normalize_sort_by("relevance") == "relevance"
    assert _normalize_sort_by("DATE") == "date"
    assert SORT_BY_VALUES == ("relevance", "date")

    with pytest.raises(ValueError) as exc:
        _normalize_sort_by("notarealsort")
    assert "notarealsort" in str(exc.value)
    assert "relevance" in str(exc.value)
    assert "date" in str(exc.value)

    # Aliases such as submittedDate stay on HOLD — reject, do not map.
    with pytest.raises(ValueError) as exc:
        _normalize_sort_by("submittedDate")
    assert "submittedDate" in str(exc.value)


@pytest.mark.asyncio
async def test_search_invalid_sort_by_returns_json_error():
    """Unknown sort_by is rejected with structured JSON (#242)."""
    result = await handle_search(
        {"query": "MoE", "max_results": 1, "sort_by": "notarealsort"}
    )
    content = json.loads(result[0].text)
    assert content["status"] == "error"
    assert "notarealsort" in content["message"]
    assert "relevance" in content["message"]
    assert "date" in content["message"]
    assert "papers" not in content


@pytest.mark.asyncio
async def test_search_no_query_optimization():
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


def test_scope_user_query_keeps_field_prefixes():
    """Explicit ti:/au:/abs: queries must not be rewritten."""
    field_query = 'ti:"transformer architecture"'
    assert _scope_user_query(field_query) == f"({field_query})"


def test_raw_search_url_groups_or_phrase_with_categories_and_date():
    """Date sort must keep (phrase OR MoE) AND categories AND date.

    Regression for issue #159: a loose OR MoE must not drop the topic
    clause and return newest papers in the selected categories.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    user_query = '"mixture of experts" OR MoE'
    logical = build_arxiv_search_query(
        user_query,
        date_from="2026-01-01",
        date_to="2026-12-31",
        categories=["cs.LG", "cs.CL"],
    )

    assert '"mixture of experts" OR MoE' in logical
    assert "(cat:cs.LG OR cat:cs.CL)" in logical
    assert "submittedDate:[202601010000 TO 202612312359]" in logical
    assert " AND " in logical
    # Unprefixed OR queries are scoped to title/abstract, not all:/au:
    assert "ti:(" in logical
    assert "abs:(" in logical
    assert "all:" not in logical
    phrase_at = logical.index('"mixture of experts" OR MoE')
    cats_at = logical.index("(cat:cs.LG OR cat:cs.CL)")
    date_at = logical.index("submittedDate:[")
    assert phrase_at < cats_at < date_at

    url = build_arxiv_search_url(
        user_query,
        max_results=5,
        sort_by="date",
        date_from="2026-01-01",
        date_to="2026-12-31",
        categories=["cs.LG", "cs.CL"],
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    search_query = params["search_query"][0]
    assert '"mixture of experts" OR MoE' in search_query
    assert "cat:cs.LG OR cat:cs.CL" in search_query
    assert "submittedDate:[202601010000 TO 202612312359]" in search_query
    assert params["sortBy"] == ["submittedDate"]
    assert params["max_results"] == ["5"]
    # Quotes must be percent-encoded so the OR group survives HTTP parsing
    assert "%22mixture" in url or "%22mixture%20of%20experts%22" in url
    assert '"mixture' not in url
    assert "%2BTO%2B" not in url
    assert "sortBy=submittedDate" in url
    decoded = unquote(url)
    assert '"mixture of experts" OR MoE' in decoded


def _atom_feed_with_totals(entry_count: int, total_results: int) -> str:
    """Build a minimal arXiv Atom feed with OpenSearch totalResults."""
    entries = []
    for i in range(entry_count):
        n = i + 1
        entries.append(f"""
        <entry>
            <id>http://arxiv.org/abs/2301.0000{n}v1</id>
            <title>Test Paper {n}</title>
            <summary>Test abstract {n}</summary>
            <published>2023-01-0{n}T00:00:00Z</published>
            <author><name>Test Author</name></author>
            <arxiv:primary_category term=\"cs.AI\"/>
            <link title=\"pdf\" href=\"http://arxiv.org/pdf/2301.0000{n}v1\"/>
        </entry>""")
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <feed xmlns=\"http://www.w3.org/2005/Atom\"
          xmlns:arxiv=\"http://arxiv.org/schemas/atom\"
          xmlns:opensearch=\"http://a9.com/-/spec/opensearch/1.1/\">
        <opensearch:totalResults>{total_results}</opensearch:totalResults>
        {''.join(entries)}
    </feed>"""


def test_parse_opensearch_total_results_from_atom_feed():
    """Atom totalResults is the corpus count, not the number of entries."""
    xml = _atom_feed_with_totals(entry_count=5, total_results=1234)
    papers = _parse_arxiv_atom_response(xml)
    assert len(papers) == 5
    assert _parse_opensearch_total_results(xml) == 1234

    payload = _build_search_response(papers, total_results=1234)
    assert payload["total_results"] == 1234
    assert payload["returned"] == 5
    assert payload["start"] == 0
    assert payload["has_more"] is True
    assert payload["next_start"] == 5
    assert len(payload["papers"]) == 5


def test_build_search_response_never_aliases_page_size_as_total():
    """A 5-paper page must not report total_results=5 when the feed says otherwise."""
    papers = [{"id": str(i)} for i in range(5)]
    payload = _build_search_response(papers, total_results=1234)
    assert payload["total_results"] == 1234
    assert payload["returned"] == 5
    assert payload["total_results"] != payload["returned"]


@pytest.mark.asyncio
async def test_search_reports_feed_total_results_not_page_size():
    """max_results=5 must not force total_results=5 when the feed says 1234."""
    mock_response = MagicMock()
    mock_response.text = _atom_feed_with_totals(entry_count=5, total_results=1234)
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await handle_search(
            {
                "query": "transformers",
                "date_from": "2020-01-01",
                "max_results": 5,
            }
        )

    content = json.loads(result[0].text)
    assert content["total_results"] == 1234
    assert content["returned"] == 5
    assert content["start"] == 0
    assert content["has_more"] is True
    assert content["next_start"] == 5
    assert len(content["papers"]) == 5
    assert content["total_results"] != content["returned"]


@pytest.mark.asyncio
async def test_search_includes_total_results_without_date_filters():
    """Issue #189: total_results present even when date_from/date_to are absent."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=4142)
    xml = xml.replace("2301.00001", "2103.12345")
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search({"query": "test query", "max_results": 1})

    content = json.loads(result[0].text)
    assert content["returned"] == 1
    assert content["start"] == 0
    assert content["total_results"] == 4142
    assert content["has_more"] is True
    assert content["next_start"] == 1
    assert len(content["papers"]) == 1
    assert content["papers"][0]["id"] == "2103.12345"


def test_build_search_response_next_start_accounts_for_offset():
    """has_more/next_start must use start + returned, not just returned."""
    papers = [{"id": str(i)} for i in range(5)]
    payload = _build_search_response(papers, total_results=14, start=10)
    assert payload["start"] == 10
    assert payload["returned"] == 5
    assert payload["total_results"] == 14
    assert payload["has_more"] is False
    assert payload["next_start"] is None

    payload = _build_search_response(papers, total_results=100, start=10)
    assert payload["has_more"] is True
    assert payload["next_start"] == 15


@pytest.mark.asyncio
async def test_raw_search_passes_start_to_arxiv_api():
    """Date-filter path must forward start to the arXiv API URL."""
    mock_response = MagicMock()
    mock_response.text = _atom_feed_with_totals(entry_count=2, total_results=14)
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await handle_search(
            {
                "query": "transformers",
                "date_from": "2020-01-01",
                "max_results": 2,
                "start": 5,
            }
        )

        from urllib.parse import parse_qs, urlparse

        url = mock_client.get.call_args[0][0]
        params = parse_qs(urlparse(url).query)
        assert params["start"] == ["5"]
        assert params["max_results"] == ["2"]

    content = json.loads(result[0].text)
    assert content["start"] == 5
    assert content["returned"] == 2
    assert content["total_results"] == 14
    assert content["has_more"] is True
    assert content["next_start"] == 7


@pytest.mark.asyncio
async def test_raw_search_end_of_results_clears_next_start():
    """Last page should set has_more=false and next_start=null."""
    mock_response = MagicMock()
    mock_response.text = _atom_feed_with_totals(entry_count=4, total_results=14)
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await handle_search(
            {
                "query": "transformers",
                "date_from": "2020-01-01",
                "max_results": 10,
                "start": 10,
            }
        )

    content = json.loads(result[0].text)
    assert content["start"] == 10
    assert content["returned"] == 4
    assert content["total_results"] == 14
    assert content["has_more"] is False
    assert content["next_start"] is None


@pytest.mark.asyncio
async def test_search_start_offset_page_two_without_dates():
    """Without dates, start is forwarded and total_results drives next_start."""
    xml = _atom_feed_with_totals(entry_count=2, total_results=100)
    # Rewrite ids to stable values
    xml = xml.replace("2301.00001", "2103.10000").replace("2301.00002", "2103.10001")
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {"query": "test query", "max_results": 2, "start": 10}
        )

        from urllib.parse import parse_qs, urlparse

        url = mock_client.get.call_args[0][0]
        params = parse_qs(urlparse(url).query)
        assert params["start"] == ["10"]
        assert params["max_results"] == ["2"]

    content = json.loads(result[0].text)
    assert content["start"] == 10
    assert content["returned"] == 2
    assert content["total_results"] == 100
    assert content["has_more"] is True
    assert content["next_start"] == 12
    assert content["papers"][0]["id"] == "2103.10000"


@pytest.mark.asyncio
async def test_search_end_of_results_no_next_start_without_dates():
    """Last page without dates: has_more false and next_start null."""
    xml = _atom_feed_with_totals(entry_count=1, total_results=21)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {"query": "test query", "max_results": 5, "start": 20}
        )

    content = json.loads(result[0].text)
    assert content["start"] == 20
    assert content["returned"] == 1
    assert content["total_results"] == 21
    assert content["has_more"] is False
    assert content["next_start"] is None


def test_search_tool_schema_includes_start():
    """Tool schema exposes optional start (>=0) for pagination."""
    from arxiv_mcp_server.tools.search import search_tool

    props = search_tool.inputSchema["properties"]
    assert "start" in props
    assert props["start"]["type"] == "integer"
    assert props["start"]["minimum"] == 0


def test_search_tool_schema_includes_abstract_mode_and_defaults():
    """Tool schema documents compact defaults and abstract_mode (#128)."""
    from arxiv_mcp_server.tools.search import search_tool

    props = search_tool.inputSchema["properties"]
    assert "abstract_mode" in props
    assert props["abstract_mode"]["enum"] == ["none", "snippet", "full"]
    assert "default: 5" in props["max_results"]["description"]
    assert "snippet" in props["abstract_mode"]["description"].lower()
    assert DEFAULT_MAX_RESULTS == 5
    assert DEFAULT_ABSTRACT_MODE == "snippet"
    assert ABSTRACT_SNIPPET_CHARS == 280
    desc = search_tool.description
    assert "max_results default 5" in desc
    assert "abstract_mode" in desc
    # Do not push agents to get_abstract after a full-abstract search.
    assert "not after abstract_mode=full" in desc  # avoid redundant get_abstract


def test_normalize_abstract_mode_defaults_and_rejects_invalid():
    assert _normalize_abstract_mode(None) == "snippet"
    assert _normalize_abstract_mode("FULL") == "full"
    assert _normalize_abstract_mode(" None ") == "none"
    try:
        _normalize_abstract_mode("summary")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "abstract_mode" in str(e)


def test_snippet_abstract_is_deterministic_bounded_and_marked():
    """Snippets are fixed-length, stable, and marked when truncated."""
    body = "A" * (ABSTRACT_SNIPPET_CHARS + 50)
    snip_a = _snippet_abstract(body)
    snip_b = _snippet_abstract(body)
    assert snip_a == snip_b
    assert snip_a.endswith("… [truncated]")
    # Body contribution before marker is exactly ABSTRACT_SNIPPET_CHARS (rstrip no-op on A's).
    assert snip_a[:ABSTRACT_SNIPPET_CHARS] == "A" * ABSTRACT_SNIPPET_CHARS
    assert len(snip_a) == ABSTRACT_SNIPPET_CHARS + len("… [truncated]")

    short = "Short abstract."
    assert _snippet_abstract(short) == short
    assert "truncated" not in _snippet_abstract(short)


def test_apply_abstract_mode_none_snippet_full():
    long_body = "Word " * 200
    papers = [
        {
            "id": "2301.00001",
            "title": "T",
            "authors": ["A"],
            "abstract": long_body,
            "categories": ["cs.AI"],
        }
    ]
    none_papers = _apply_abstract_mode(papers, "none")
    assert "abstract" not in none_papers[0]
    assert none_papers[0]["title"] == "T"

    snip_papers = _apply_abstract_mode(papers, "snippet")
    assert snip_papers[0]["abstract"].endswith("… [truncated]")
    assert len(snip_papers[0]["abstract"]) < len(papers[0]["abstract"])

    full_papers = _apply_abstract_mode(papers, "full")
    assert full_papers[0]["abstract"] == papers[0]["abstract"]
    # Inputs must not be mutated
    assert "abstract" in papers[0]


@pytest.mark.asyncio
async def test_search_defaults_to_five_compact_snippets():
    """Omitting max_results/abstract_mode yields ≤5 snippet results (#128)."""
    long_summary = "Z" * (ABSTRACT_SNIPPET_CHARS + 40)
    entries = []
    for i in range(5):
        n = i + 1
        entries.append(f"""
        <entry>
            <id>http://arxiv.org/abs/2301.0000{n}v1</id>
            <title>Test Paper {n}</title>
            <summary>{long_summary}</summary>
            <published>2023-01-0{n}T00:00:00Z</published>
            <author><name>Test Author</name></author>
            <arxiv:primary_category term="cs.AI"/>
            <link title="pdf" href="http://arxiv.org/pdf/2301.0000{n}v1"/>
        </entry>""")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <opensearch:totalResults>42</opensearch:totalResults>
        {''.join(entries)}
    </feed>"""
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search({"query": "transformers"})

    from urllib.parse import parse_qs, urlparse

    url = mock_client.get.call_args[0][0]
    params = parse_qs(urlparse(url).query)
    assert params["max_results"] == ["5"]
    assert params["start"] == ["0"]

    content = json.loads(result[0].text)
    assert content["returned"] == 5
    assert content["abstract_mode"] == "snippet"
    assert content["total_results"] == 42
    assert content["has_more"] is True
    assert content["next_start"] == 5
    assert len(content["papers"]) == 5
    for paper in content["papers"]:
        assert paper["abstract"].endswith("… [truncated]")
        assert "title" in paper and "authors" in paper


@pytest.mark.asyncio
async def test_search_abstract_mode_none_omits_abstracts():
    xml = _atom_feed_with_totals(entry_count=2, total_results=2)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {"query": "test", "max_results": 2, "abstract_mode": "none"}
        )

    content = json.loads(result[0].text)
    assert content["abstract_mode"] == "none"
    assert content["returned"] == 2
    for paper in content["papers"]:
        assert "abstract" not in paper
        assert paper["title"]
        assert paper["authors"]


@pytest.mark.asyncio
async def test_search_abstract_mode_full_keeps_complete_abstract():
    long_summary = "Complete abstract body. " * 30
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <opensearch:totalResults>1</opensearch:totalResults>
        <entry>
            <id>http://arxiv.org/abs/2301.00001v1</id>
            <title>Full Mode Paper</title>
            <summary>{long_summary}</summary>
            <published>2023-01-01T00:00:00Z</published>
            <author><name>Test Author</name></author>
            <arxiv:primary_category term="cs.AI"/>
            <link title="pdf" href="http://arxiv.org/pdf/2301.00001v1"/>
        </entry>
    </feed>"""
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {"query": "test", "max_results": 1, "abstract_mode": "full"}
        )

    content = json.loads(result[0].text)
    assert content["abstract_mode"] == "full"
    abstract = content["papers"][0]["abstract"]
    assert "EXTERNAL CONTENT" not in abstract
    assert "UNTRUSTED EXTERNAL CONTENT" in content["content_warning"]
    assert "truncated" not in abstract
    assert long_summary.strip().replace("\n", " ")[:40] in abstract.replace("\n", " ")


@pytest.mark.asyncio
async def test_search_legacy_explicit_max_results_and_full_abstract():
    """Explicit legacy-style args still work (max_results=10, abstract_mode=full)."""
    xml = _atom_feed_with_totals(entry_count=3, total_results=3)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {
                "query": "legacy",
                "max_results": 10,
                "abstract_mode": "full",
            }
        )

    from urllib.parse import parse_qs, urlparse

    url = mock_client.get.call_args[0][0]
    params = parse_qs(urlparse(url).query)
    assert params["max_results"] == ["10"]

    content = json.loads(result[0].text)
    assert content["abstract_mode"] == "full"
    assert content["returned"] == 3
    assert "EXTERNAL CONTENT" not in content["papers"][0]["abstract"]
    assert "UNTRUSTED EXTERNAL CONTENT" in content["content_warning"]


@pytest.mark.asyncio
async def test_search_continuation_preserves_abstract_mode_and_offset():
    """Page 2 via next_start does not skip/dupe under a stable feed (#128/#186)."""
    xml_page1 = _atom_feed_with_totals(entry_count=2, total_results=5)
    xml_page1 = xml_page1.replace("2301.00001", "2103.10001").replace(
        "2301.00002", "2103.10002"
    )
    xml_page2 = _atom_feed_with_totals(entry_count=2, total_results=5)
    xml_page2 = xml_page2.replace("2301.00001", "2103.10003").replace(
        "2301.00002", "2103.10004"
    )
    xml_page3 = _atom_feed_with_totals(entry_count=1, total_results=5)
    xml_page3 = xml_page3.replace("2301.00001", "2103.10005")

    pages = [xml_page1, xml_page2, xml_page3]
    observed_starts = []

    mock_client = AsyncMock()

    async def capture_get(url, **kwargs):
        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(url).query)
        start = int(params["start"][0])
        observed_starts.append(start)
        resp = MagicMock()
        resp.text = pages[len(observed_starts) - 1]
        resp.raise_for_status = MagicMock()
        return resp

    mock_client.get = AsyncMock(side_effect=capture_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        page1 = json.loads(
            (
                await handle_search(
                    {
                        "query": "page",
                        "max_results": 2,
                        "abstract_mode": "none",
                    }
                )
            )[0].text
        )
        assert page1["start"] == 0
        assert page1["next_start"] == 2
        assert page1["abstract_mode"] == "none"
        ids1 = [p["id"] for p in page1["papers"]]

        page2 = json.loads(
            (
                await handle_search(
                    {
                        "query": "page",
                        "max_results": 2,
                        "start": page1["next_start"],
                        "abstract_mode": page1["abstract_mode"],
                    }
                )
            )[0].text
        )
        assert page2["start"] == 2
        assert page2["next_start"] == 4
        ids2 = [p["id"] for p in page2["papers"]]

        page3 = json.loads(
            (
                await handle_search(
                    {
                        "query": "page",
                        "max_results": 2,
                        "start": page2["next_start"],
                        "abstract_mode": "none",
                    }
                )
            )[0].text
        )
        assert page3["start"] == 4
        assert page3["has_more"] is False
        assert page3["next_start"] is None
        ids3 = [p["id"] for p in page3["papers"]]

    assert observed_starts == [0, 2, 4]
    all_ids = ids1 + ids2 + ids3
    assert all_ids == [
        "2103.10001",
        "2103.10002",
        "2103.10003",
        "2103.10004",
        "2103.10005",
    ]
    assert len(all_ids) == len(set(all_ids))


@pytest.mark.asyncio
async def test_search_empty_page_past_end():
    """Empty upstream page: returned=0, has_more false, next_start null."""
    xml = _atom_feed_with_totals(entry_count=0, total_results=10)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {
                "query": "empty",
                "max_results": 5,
                "start": 50,
                "abstract_mode": "snippet",
            }
        )

    content = json.loads(result[0].text)
    assert content["start"] == 50
    assert content["returned"] == 0
    assert content["papers"] == []
    assert content["total_results"] == 10
    assert content["has_more"] is False
    assert content["next_start"] is None
    assert content["abstract_mode"] == "snippet"


@pytest.mark.asyncio
async def test_search_invalid_abstract_mode_errors():
    result = await handle_search({"query": "x", "abstract_mode": "brief"})
    content = json.loads(result[0].text)
    assert content["status"] == "error"
    assert "abstract_mode" in content["message"]
    assert not result[0].text.startswith("Error:")


def test_build_search_response_echoes_abstract_mode():
    papers = [{"id": "1"}]
    payload = _build_search_response(papers, total_results=1, abstract_mode="full")
    assert payload["abstract_mode"] == "full"
    assert payload["next_start"] is None


@pytest.mark.asyncio
async def test_search_emits_content_warning_once_not_per_abstract():
    """One content_warning per response; abstracts stay prefix-free (#230)."""
    xml = _atom_feed_with_totals(entry_count=3, total_results=3)
    _, mock_client = _mock_httpx_response(xml)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await handle_search(
            {"query": "test", "max_results": 3, "abstract_mode": "snippet"}
        )

    content = json.loads(result[0].text)
    assert "UNTRUSTED EXTERNAL CONTENT" in content["content_warning"]
    assert len(content["content_warning"]) < 80
    assert content["returned"] == 3
    for paper in content["papers"]:
        assert "EXTERNAL CONTENT" not in paper["abstract"]
        assert "UNTRUSTED" not in paper["abstract"]

    # abstract_mode=none skips the response-level warning (no abstracts returned).
    with patch("httpx.AsyncClient", return_value=mock_client):
        none_result = await handle_search(
            {"query": "test", "max_results": 3, "abstract_mode": "none"}
        )
    none_content = json.loads(none_result[0].text)
    assert "content_warning" not in none_content
    assert "abstract" not in none_content["papers"][0]


@pytest.mark.asyncio
async def test_search_no_criteria_returns_structured_error():
    """Empty query with no filters must return {status, message} JSON (#238)."""
    result = await handle_search({"query": "   "})
    content = json.loads(result[0].text)
    assert content["status"] == "error"
    assert content["message"] == "No search criteria provided"
    assert not result[0].text.startswith("Error:")


def test_search_backoff_seconds_matches_citation_graph_pattern():
    """Backoff ladder should use exponential delay with jitter (#238)."""
    with patch.object(search_module.random, "random", return_value=0.5):
        assert _backoff_seconds(0, None) == 2.0
        assert _backoff_seconds(1, None) == 4.0
        assert _backoff_seconds(2, None) == 8.0
        assert _backoff_seconds(5, None) == 60.0

    with patch.object(search_module.random, "random", return_value=0.0):
        assert _backoff_seconds(0, None) == 1.0

    with patch.object(search_module.random, "random", return_value=1.0):
        assert _backoff_seconds(0, "45") == 60.0


@pytest.mark.asyncio
async def test_search_retries_on_429_then_succeeds():
    """A transient arXiv 429 should be retried with backoff, then succeed (#238)."""
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {}
    rate_limited.text = ""
    rate_limited.raise_for_status = MagicMock()

    xml = _atom_feed_with_totals(entry_count=1, total_results=1)
    ok = MagicMock()
    ok.status_code = 200
    ok.headers = {}
    ok.text = xml
    ok.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[rate_limited, ok])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(search_module.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        patch.object(search_module.random, "random", return_value=0.5),
    ):
        result = await handle_search({"query": "transformers", "max_results": 1})

    content = json.loads(result[0].text)
    assert "papers" in content
    assert content["returned"] == 1
    assert mock_client.get.call_count == 2
    sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_search_429_exhausted_returns_soft_rate_limited():
    """Persistent arXiv 429s soft-fail as status=rate_limited JSON (#238)."""
    attempts = _MAX_RETRIES + 1
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {"Retry-After": "30"}
    rate_limited.text = ""
    rate_limited.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=rate_limited)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(search_module.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        patch.object(search_module.random, "random", return_value=0.5),
    ):
        result = await handle_search({"query": "transformers", "max_results": 1})

    content = json.loads(result[0].text)
    assert content["status"] == "rate_limited"
    assert "HTTP 429" in content["message"]
    assert content["http_status"] == 429
    assert content["retry_after_seconds"] == 30.0
    assert not result[0].text.startswith("Error:")
    assert mock_client.get.call_count == attempts
    assert sleep.await_count == _MAX_RETRIES


@pytest.mark.asyncio
async def test_rate_limited_get_retries_503_then_succeeds():
    """503 follows the same retry path as 429 in the shared client (#238)."""
    limited = MagicMock()
    limited.status_code = 503
    limited.headers = {}
    limited.text = ""
    limited.raise_for_status = MagicMock()

    ok = MagicMock()
    ok.status_code = 200
    ok.headers = {}
    ok.text = "<feed/>"
    ok.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[limited, ok])

    with (
        patch.object(search_module.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        patch.object(search_module.random, "random", return_value=0.5),
    ):
        response = await _rate_limited_get(
            mock_client, "https://export.arxiv.org/api/query"
        )

    assert response is ok
    assert mock_client.get.call_count == 2
    sleep.assert_awaited_once_with(2.0)
