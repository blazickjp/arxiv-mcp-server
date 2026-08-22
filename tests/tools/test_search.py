"""Tests for paper search functionality."""

import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from arxiv_mcp_server.tools import handle_search
from arxiv_mcp_server.tools import search as search_module
from arxiv_mcp_server.tools.search import (
    _validate_categories,
    _raw_arxiv_search,
    _parse_arxiv_atom_response,
    _parse_opensearch_total_results,
    _build_search_response,
    _scope_user_query,
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


@pytest.mark.asyncio
async def test_basic_search(mock_client):
    """Test basic paper search functionality."""
    with patch("arxiv.Client", return_value=mock_client):
        result = await handle_search({"query": "test query", "max_results": 1})

        assert len(result) == 1
        content = json.loads(result[0].text)
        assert content["returned"] == 1
        assert content["start"] == 0
        assert "total_results" not in content
        assert content["has_more"] is False
        assert content["next_start"] is None
        paper = content["papers"][0]
        assert paper["id"] == "2103.12345"
        assert paper["title"] == "Test Paper"
        assert "resource_uri" in paper


@pytest.mark.asyncio
async def test_package_search_uses_process_wide_rate_limiter(mock_client, mocker):
    """The arxiv package path must share the same gate as raw API requests."""
    mocker.patch.object(search_module, "get_arxiv_client", return_value=mock_client)
    run_sync = mocker.patch.object(
        search_module.ARXIV_RATE_LIMITER,
        "run_sync",
        side_effect=lambda operation: operation(),
    )

    await handle_search({"query": "test query", "max_results": 1})

    run_sync.assert_called_once()


@pytest.mark.asyncio
async def test_search_with_categories(mock_client):
    """Test paper search with category filtering."""
    with patch("arxiv.Client", return_value=mock_client):
        result = await handle_search(
            {"query": "test query", "categories": ["cs.AI", "cs.LG"], "max_results": 1}
        )

        content = json.loads(result[0].text)
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
    assert paper["abstract"] == "[EXTERNAL CONTENT] This is a test abstract."
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
async def test_search_with_invalid_categories(mock_client):
    """Test search with invalid categories."""
    with patch("arxiv.Client", return_value=mock_client):
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
    with patch("arxiv.Client", return_value=mock_client):
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
    with patch("arxiv.Client", return_value=mock_client):
        # Test that very large max_results gets capped
        result = await handle_search({"query": "test", "max_results": 1000})

        # Should not fail and should be limited by settings.MAX_RESULTS
        content = json.loads(result[0].text)
        assert "papers" in content


@pytest.mark.asyncio
async def test_search_reuses_client_and_updates_page_size_inside_gate(
    mock_client, mock_paper, monkeypatch
):
    """Varying result limits must reuse one client without stale page sizes."""
    from arxiv_mcp_server import config

    monkeypatch.setattr(config, "_arxiv_client", None)
    observed_page_sizes = []

    def results(_search, offset=0):
        observed_page_sizes.append(mock_client.page_size)
        return [mock_paper]

    mock_client.page_size = 100
    mock_client.results.side_effect = results
    with patch("arxiv.Client", return_value=mock_client) as mock_client_class:
        await handle_search({"query": "first", "max_results": 5})
        await handle_search({"query": "second", "max_results": 7})

    mock_client_class.assert_called_once_with()
    # Client path requests max_results+1 so has_more is not a page-size guess.
    assert observed_page_sizes == [6, 8]


@pytest.mark.asyncio
async def test_search_sort_by_relevance(mock_client):
    """Test search with relevance sorting (default)."""
    with patch("arxiv.Client", return_value=mock_client):
        result = await handle_search({"query": "test", "sort_by": "relevance"})

        content = json.loads(result[0].text)
        assert "papers" in content


@pytest.mark.asyncio
async def test_search_sort_by_date(mock_client):
    """Test search with date sorting."""
    with patch("arxiv.Client", return_value=mock_client):
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
async def test_client_path_has_more_from_extra_fetched_paper(mock_paper):
    """Without Atom totals, the client path uses max_results+1 to set has_more."""
    extra = MagicMock()
    extra.get_short_id.return_value = "2103.99999"
    extra.title = "Extra Paper"
    extra.authors = mock_paper.authors
    extra.summary = "Extra abstract"
    extra.categories = ["cs.LG"]
    extra.published = mock_paper.published
    extra.pdf_url = "https://arxiv.org/pdf/2103.99999"

    client = MagicMock()
    client.results.return_value = [mock_paper, extra]

    with patch("arxiv_mcp_server.tools.search.get_arxiv_client", return_value=client):
        result = await handle_search({"query": "test query", "max_results": 1})

    content = json.loads(result[0].text)
    assert content["returned"] == 1
    assert content["start"] == 0
    assert content["has_more"] is True
    assert content["next_start"] == 1
    assert "total_results" not in content
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
async def test_client_path_start_offset_page_two(mock_paper):
    """Client path must pass offset=start and return next_start for page 2."""
    papers = []
    for i in range(3):
        p = MagicMock()
        p.get_short_id.return_value = f"2103.1000{i}"
        p.title = f"Paper {i}"
        p.authors = mock_paper.authors
        p.summary = f"Abstract {i}"
        p.categories = ["cs.LG"]
        p.published = mock_paper.published
        p.pdf_url = f"https://arxiv.org/pdf/2103.1000{i}"
        papers.append(p)

    client = MagicMock()
    # max_results=2 requests fetch_limit=3; 3 papers => has_more
    client.results.return_value = papers

    with patch("arxiv_mcp_server.tools.search.get_arxiv_client", return_value=client):
        result = await handle_search(
            {"query": "test query", "max_results": 2, "start": 10}
        )

    args, kwargs = client.results.call_args
    assert kwargs.get("offset", args[1] if len(args) > 1 else None) == 10
    search_arg = args[0]
    assert search_arg.max_results == 13  # start + max_results + 1

    content = json.loads(result[0].text)
    assert content["start"] == 10
    assert content["returned"] == 2
    assert content["has_more"] is True
    assert content["next_start"] == 12
    assert content["papers"][0]["id"] == "2103.10000"


@pytest.mark.asyncio
async def test_client_path_end_of_results_no_next_start(mock_paper):
    """Client path end page: fewer than max_results => next_start null."""
    client = MagicMock()
    client.results.return_value = [mock_paper]  # only one left

    with patch("arxiv_mcp_server.tools.search.get_arxiv_client", return_value=client):
        result = await handle_search(
            {"query": "test query", "max_results": 5, "start": 20}
        )

    content = json.loads(result[0].text)
    assert content["start"] == 20
    assert content["returned"] == 1
    assert content["has_more"] is False
    assert content["next_start"] is None


def test_search_tool_schema_includes_start():
    """Tool schema exposes optional start (>=0) for pagination."""
    from arxiv_mcp_server.tools.search import search_tool

    props = search_tool.inputSchema["properties"]
    assert "start" in props
    assert props["start"]["type"] == "integer"
    assert props["start"]["minimum"] == 0
