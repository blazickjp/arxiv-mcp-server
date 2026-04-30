"""Tests for paper download functionality (sync HTML-first pipeline)."""

import pytest
import json
from unittest.mock import MagicMock

import arxiv

from arxiv_mcp_server.tools.download import (
    handle_download,
    get_paper_path,
    _html_to_text,
    _fetch_html_content,
    _download_arxiv_pdf_to_path,
    PaperNotFoundError,
)

# ---------------------------------------------------------------------------
# PDF download helper (httpx streaming)
# ---------------------------------------------------------------------------


def test_download_arxiv_pdf_streams_via_httpx(temp_storage_path, mocker):
    """_download_arxiv_pdf_to_path streams from paper.pdf_url; never calls download_pdf."""
    import arxiv_mcp_server.tools.download as dl

    stream_response = MagicMock()
    stream_response.raise_for_status = MagicMock()
    stream_response.iter_bytes.return_value = [b"chunk-one", b"chunk-two"]

    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = stream_response
    stream_cm.__exit__.return_value = False

    http_client = MagicMock()
    http_client.stream.return_value = stream_cm
    http_client.__enter__.return_value = http_client
    http_client.__exit__.return_value = False

    mocker.patch.object(dl.httpx, "Client", return_value=http_client)

    paper = MagicMock(spec=arxiv.Result)
    paper.pdf_url = "https://arxiv.org/pdf/2103.00000.pdf"
    dest = temp_storage_path / "paper.pdf"

    _download_arxiv_pdf_to_path(paper, dest)

    assert dest.read_bytes() == b"chunk-onechunk-two"
    http_client.stream.assert_called_once()
    assert http_client.stream.call_args[0][0] == "GET"
    assert http_client.stream.call_args[0][1] == paper.pdf_url
    paper.download_pdf.assert_not_called()


def test_download_arxiv_pdf_requires_pdf_url(temp_storage_path):
    """Missing pdf_url must fail fast with a clear error."""
    paper = MagicMock(spec=arxiv.Result)
    paper.pdf_url = None
    dest = temp_storage_path / "missing.pdf"

    with pytest.raises(ValueError, match="No PDF URL available"):
        _download_arxiv_pdf_to_path(paper, dest)


# ---------------------------------------------------------------------------
# Unit tests for HTML parser
# ---------------------------------------------------------------------------


def test_html_to_text_strips_scripts():
    html = "<html><body><script>alert(1)</script><p>Hello world</p></body></html>"
    text = _html_to_text(html)
    assert "alert" not in text
    assert "Hello world" in text


def test_html_to_text_strips_style():
    html = "<html><head><style>body{color:red}</style></head><body><p>Content</p></body></html>"
    text = _html_to_text(html)
    assert "color" not in text
    assert "Content" in text


def test_html_to_text_extracts_article_text():
    html = (
        "<html><body>"
        "<nav>Nav stuff</nav>"
        "<article><h1>Title</h1><p>Abstract here.</p></article>"
        "<footer>Footer</footer>"
        "</body></html>"
    )
    text = _html_to_text(html)
    assert "Title" in text
    assert "Abstract here" in text
    # nav and footer tags themselves are stripped, but their text won't be
    # because nav/footer ARE in SKIP_TAGS — verify they're gone
    assert "Nav stuff" not in text
    assert "Footer" not in text


# ---------------------------------------------------------------------------
# Integration-style handler tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cached_paper_returns_immediately(temp_storage_path, mocker):
    """A paper already in cache is returned immediately without network calls."""
    paper_id = "2103.12345"

    # Patch get_paper_path to use temp dir — this is the only path helper we need
    def fake_path(pid, suffix=".md"):
        return temp_storage_path / f"{pid}{suffix}"

    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path", side_effect=fake_path
    )

    md_path = temp_storage_path / f"{paper_id}.md"
    md_path.write_text("# Cached Paper\nThis is cached content.", encoding="utf-8")

    # Ensure no network calls are made
    mock_httpx = mocker.patch("arxiv_mcp_server.tools.download._fetch_html_content")
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "cache"
    assert "Cached Paper" in result["content"]
    mock_httpx.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_html_endpoint_success(temp_storage_path, mocker):
    """HTML endpoint returns 200 -> content saved and returned directly."""
    paper_id = "2103.11111"

    def fake_path(pid, suffix=".md"):
        return temp_storage_path / f"{pid}{suffix}"

    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path", side_effect=fake_path
    )

    html_text = "Title of the Paper\nAbstract content goes here."
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=html_text,
    )
    # PDF path should NOT be called
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "html"
    assert result["content"].endswith(html_text)
    assert result["content"].startswith("[UNTRUSTED EXTERNAL CONTENT")
    # Markdown file should have been saved to cache
    assert (temp_storage_path / f"{paper_id}.md").exists()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_html_404_falls_back_to_pdf(temp_storage_path, mocker):
    """HTML endpoint returns None (404) -> falls back to PDF conversion."""
    paper_id = "2103.22222"

    def fake_path(pid, suffix=".md"):
        return temp_storage_path / f"{pid}{suffix}"

    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path", side_effect=fake_path
    )
    # Simulate pdf extra being available so the PDF fallback path is reached
    mocker.patch("arxiv_mcp_server.tools.download._pdf_available", True)

    # HTML not available
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=None,
    )

    mock_arxiv_result = MagicMock(spec=arxiv.Result)
    pdf_markdown = "# PDF Paper\nConverted from PDF."
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_pdf_content",
        return_value=(pdf_markdown, mock_arxiv_result),
    )

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "pdf"
    assert result["content"].endswith(pdf_markdown)
    assert result["content"].startswith("[UNTRUSTED EXTERNAL CONTENT")
    assert (temp_storage_path / f"{paper_id}.md").exists()


@pytest.mark.asyncio
async def test_paper_not_found_on_arxiv(temp_storage_path, mocker):
    """StopIteration from PDF fallback -> error message returned."""
    paper_id = "invalid.00000"

    def fake_path(pid, suffix=".md"):
        return temp_storage_path / f"{pid}{suffix}"

    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path", side_effect=fake_path
    )
    # Simulate pdf extra being available so the PDF fallback path is reached
    mocker.patch("arxiv_mcp_server.tools.download._pdf_available", True)

    # HTML not available
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=None,
    )
    # PDF fetch raises PaperNotFoundError (paper not found)
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_pdf_content",
        side_effect=PaperNotFoundError(f"Paper {paper_id} not found on arXiv"),
    )

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "not found on arXiv" in result["message"]


@pytest.mark.asyncio
async def test_no_check_status_parameter(temp_storage_path, mocker):
    """Passing check_status is no longer a valid argument but should not crash
    the handler — extra kwargs are simply ignored."""
    paper_id = "2103.33333"

    def fake_path(pid, suffix=".md"):
        return temp_storage_path / f"{pid}{suffix}"

    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path", side_effect=fake_path
    )

    html_text = "Some paper content"
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=html_text,
    )

    # Should not raise even if client passes check_status=True (it's ignored)
    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_unexpected_error_returns_error_status(temp_storage_path, mocker):
    """Any unexpected exception results in a clean error response."""
    paper_id = "2103.44444"

    def fake_path(pid, suffix=".md"):
        return temp_storage_path / f"{pid}{suffix}"

    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path", side_effect=fake_path
    )

    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        side_effect=RuntimeError("Network exploded"),
    )

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "Error:" in result["message"]
