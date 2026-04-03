"""Tests for paper download functionality (sync HTML-first pipeline)."""

import pytest
import json
from unittest.mock import MagicMock, patch
from pathlib import Path

import arxiv
import httpx

from arxiv_mcp_server.tools.download import (
    handle_download,
    get_paper_path,
    _html_to_text,
    _fetch_html_content,
    PaperNotFoundError,
)

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
    assert result["content"] == html_text
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
    assert result["content"] == pdf_markdown
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
