"""Comprehensive tests for the get_abstract tool."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from arxiv_mcp_server.tools.get_abstract import (
    abstract_tool,
    handle_get_abstract,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FULL_ATOM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>  Attention Is All You Need  </title>
    <summary>  We propose a novel model.
Spanning multiple lines.  </summary>
    <published>2024-01-15T00:00:00Z</published>
    <author><name>Alice Alpha</name></author>
    <author><name>Bob Beta</name></author>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <category term="cs.AI"/>
    <link title="pdf" href="https://arxiv.org/pdf/2401.12345v1"/>
  </entry>
</feed>
"""

NO_ENTRIES_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
</feed>
"""

NO_PDF_LINK_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.99999v1</id>
    <title>No PDF Link Paper</title>
    <summary>Abstract here.</summary>
    <published>2024-02-01T00:00:00Z</published>
    <author><name>Carol Gamma</name></author>
    <arxiv:primary_category term="math.CO"/>
    <category term="math.CO"/>
  </entry>
</feed>
"""

SINGLE_AUTHOR_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2310.00001v2</id>
    <title>Single Author Paper</title>
    <summary>Only one author wrote this.</summary>
    <published>2023-10-01T00:00:00Z</published>
    <author><name>Sole Author</name></author>
    <arxiv:primary_category term="physics.gen-ph"/>
    <link title="pdf" href="https://arxiv.org/pdf/2310.00001v2"/>
  </entry>
</feed>
"""

CATEGORY_DEDUP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2312.55555v1</id>
    <title>Category Dedup Test</title>
    <summary>Tests category deduplication.</summary>
    <published>2023-12-01T00:00:00Z</published>
    <author><name>Dev Tester</name></author>
    <arxiv:primary_category term="cs.CL"/>
    <category term="cs.CL"/>
    <category term="cs.AI"/>
    <category term="cs.AI"/>
    <link title="pdf" href="https://arxiv.org/pdf/2312.55555v1"/>
  </entry>
</feed>
"""


def _make_mock_response(xml_text: str) -> MagicMock:
    """Build a fake httpx Response with .text set."""
    resp = MagicMock()
    resp.text = xml_text
    return resp


def _mock_rate_limited_get(xml_text: str):
    """Return an AsyncMock that resolves to a fake response."""
    return AsyncMock(return_value=_make_mock_response(xml_text))


# ---------------------------------------------------------------------------
# Tool metadata / schema tests
# ---------------------------------------------------------------------------


def test_abstract_tool_name():
    assert abstract_tool.name == "get_abstract"


def test_abstract_tool_description_mentions_workflow():
    desc = abstract_tool.description
    assert "abstract" in desc.lower()
    assert "download" in desc.lower()


def test_abstract_tool_input_schema_has_paper_id():
    schema = abstract_tool.inputSchema
    assert "paper_id" in schema["properties"]
    assert "paper_id" in schema["required"]


def test_abstract_tool_annotations_readonly():
    assert abstract_tool.annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# Empty / blank paper_id validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_paper_id_returns_error():
    result = await handle_get_abstract({"paper_id": ""})
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert "paper_id is required" in data["message"]


@pytest.mark.asyncio
async def test_whitespace_only_paper_id_returns_error():
    """paper_id consisting only of whitespace is stripped to empty → error."""
    result = await handle_get_abstract({"paper_id": "   "})
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert "paper_id is required" in data["message"]


# ---------------------------------------------------------------------------
# Successful fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_fetch_returns_success_status(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["status"] == "success"


@pytest.mark.asyncio
async def test_successful_fetch_returns_paper_id(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["paper_id"] == "2401.12345"


@pytest.mark.asyncio
async def test_successful_fetch_title_stripped(mocker):
    """Title whitespace and leading/trailing spaces are stripped."""
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["title"] == "Attention Is All You Need"


@pytest.mark.asyncio
async def test_successful_fetch_abstract_newlines_replaced(mocker):
    """Newlines inside the abstract text are replaced with spaces."""
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert "\n" not in data["abstract"]
    assert "We propose a novel model." in data["abstract"]


@pytest.mark.asyncio
async def test_successful_fetch_authors_list(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["authors"] == ["Alice Alpha", "Bob Beta"]


@pytest.mark.asyncio
async def test_successful_fetch_categories(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    # primary comes first, then additional unique categories
    assert data["categories"][0] == "cs.LG"
    assert "cs.AI" in data["categories"]


@pytest.mark.asyncio
async def test_successful_fetch_published_date(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["published"] == "2024-01-15T00:00:00Z"


@pytest.mark.asyncio
async def test_successful_fetch_pdf_url_from_link(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["pdf_url"] == "https://arxiv.org/pdf/2401.12345v1"


@pytest.mark.asyncio
async def test_result_is_list_of_one_text_content(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    assert len(result) == 1
    assert result[0].type == "text"


# ---------------------------------------------------------------------------
# PDF URL fallback (no <link title="pdf"> element)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_url_fallback_when_no_link_element(mocker):
    """When no pdf link is present, pdf_url falls back to arxiv.org/pdf/{id}."""
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(NO_PDF_LINK_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.99999"})
    data = json.loads(result[0].text)
    assert data["status"] == "success"
    assert data["pdf_url"] == "https://arxiv.org/pdf/2401.99999"


# ---------------------------------------------------------------------------
# Single author
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_author_returns_list(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(SINGLE_AUTHOR_XML),
    )
    result = await handle_get_abstract({"paper_id": "2310.00001"})
    data = json.loads(result[0].text)
    assert isinstance(data["authors"], list)
    assert data["authors"] == ["Sole Author"]


# ---------------------------------------------------------------------------
# Category deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_category_deduplication(mocker):
    """Duplicate category terms should appear only once."""
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(CATEGORY_DEDUP_XML),
    )
    result = await handle_get_abstract({"paper_id": "2312.55555"})
    data = json.loads(result[0].text)
    cats = data["categories"]
    assert cats.count("cs.AI") == 1
    assert cats.count("cs.CL") == 1


# ---------------------------------------------------------------------------
# Paper not found (empty feed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_not_found_returns_error(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(NO_ENTRIES_XML),
    )
    result = await handle_get_abstract({"paper_id": "9999.00000"})
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert "9999.00000" in data["message"]
    assert "not found" in data["message"].lower()


# ---------------------------------------------------------------------------
# Rate-limit / timeout error (RuntimeError from _rate_limited_get)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_error_returns_error_status(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        AsyncMock(side_effect=RuntimeError("Rate limit exceeded")),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert "Rate limit exceeded" in data["message"]


@pytest.mark.asyncio
async def test_timeout_runtime_error_handled(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        AsyncMock(side_effect=RuntimeError("Request timed out after 3 retries")),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert "timed out" in data["message"]


# ---------------------------------------------------------------------------
# Generic / unexpected exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_returns_error_status(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        AsyncMock(side_effect=ValueError("unexpected boom")),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert "unexpected boom" in data["message"]


@pytest.mark.asyncio
async def test_network_error_returns_error_status(mocker):
    import httpx

    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        AsyncMock(side_effect=httpx.NetworkError("connection refused")),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["status"] == "error"


# ---------------------------------------------------------------------------
# Malformed XML
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_xml_returns_error(mocker):
    """ET.fromstring will raise ParseError for bad XML → caught by generic handler."""
    bad_xml = "<<< not valid xml >>>"
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(bad_xml),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert data["status"] == "error"


# ---------------------------------------------------------------------------
# paper_id with leading/trailing whitespace is stripped before use
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_id_is_stripped_before_url_construction(mocker):
    """paper_id with surrounding whitespace should still work (stripped internally)."""
    mock_get = _mock_rate_limited_get(FULL_ATOM_XML)
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        mock_get,
    )
    result = await handle_get_abstract({"paper_id": "  2401.12345  "})
    data = json.loads(result[0].text)
    # paper_id in response should be the stripped version
    assert data["paper_id"] == "2401.12345"
    assert data["status"] == "success"


# ---------------------------------------------------------------------------
# JSON output structure sanity check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_response_has_all_required_keys(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        _mock_rate_limited_get(FULL_ATOM_XML),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    for key in (
        "status",
        "paper_id",
        "title",
        "authors",
        "abstract",
        "categories",
        "published",
        "pdf_url",
    ):
        assert key in data, f"Missing key: {key}"


@pytest.mark.asyncio
async def test_error_response_has_status_and_message_keys(mocker):
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    result = await handle_get_abstract({"paper_id": "2401.12345"})
    data = json.loads(result[0].text)
    assert "status" in data
    assert "message" in data
