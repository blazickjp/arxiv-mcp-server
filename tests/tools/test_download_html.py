"""HTML chrome/math and download handler tests (restored for #175)."""

import json
from unittest.mock import MagicMock

import arxiv
import pytest

from arxiv_mcp_server.tools.download import (
    EXTRACTOR_VERSION,
    PaperNotFoundError,
    _html_to_text,
    handle_download,
)


ARXIV_CHROME_FIXTURE = """
<html>
  <head><title>Attention Is All You Need</title></head>
  <body>
    <dialog id="modal-form">
      <form>
        <h5>Report GitHub Issue</h5>
        <label>Title:</label>
        <input id="form_title" name="form_title" placeholder="Enter title">
        <p>Content selection saved. Describe the issue below:</p>
        <label>Description:</label>
      </form>
    </dialog>
    <div class="ds-announcement" id="announcement-banner">
      <span>arXiv is now an independent nonprofit!</span>
      <a>Learn more</a>
      <button aria-label="Dismiss announcement">×</button>
    </div>
    <header class="arxiv-html-header">
      <span class="sr-only">Back to arXiv</span>
    </header>
    <nav>Why HTML?</nav>
    <div class="infobox" id="infobox">
      <div id="watermark-tr">arXiv:1706.03762v7 [cs.CL] 02 Aug 2023</div>
    </div>
    <article class="ltx_document">
      <h1 class="ltx_title">Attention Is All You Need</h1>
      <div class="ltx_authors">
        <span class="ltx_personname">Ashish Vaswani</span>
        <span class="ltx_author_notes">
          <span class="ltx_contact ltx_role_thanks">
            <span class="ltx_contact_name">Thanks:</span>
            ORCID: 0009-0009-0452-9407
          </span>
          <span class="ltx_contact ltx_role_affiliation">
            <span class="ltx_contact_name">Affiliation:</span> Google Brain
          </span>
          <span class="ltx_contact ltx_role_email">
            <span class="ltx_contact_name">Email:</span> avaswani@google.com
          </span>
        </span>
      </div>
      <p>Paper body sentence about attention.</p>
    </article>
  </body>
</html>
"""

ARXIV_MATH_FIXTURE = """
<html>
  <body>
    <article>
      <p>measured decode is
        <math class="ltx_Math" alttext="0.44" display="inline">
          <semantics>
            <mn>0.44</mn>
            <annotation encoding="application/x-tex">0.44</annotation>
          </semantics>
        </math>
        tok/s warm, reuse is
        <math class="ltx_Math" alttext="2.0\\times" display="inline">
          <semantics>
            <mrow><mn>2.0</mn><mo>×</mo></mrow>
            <annotation encoding="application/x-tex">2.0\\times</annotation>
          </semantics>
        </math>
        chance.</p>
    </article>
  </body>
</html>
"""


def test_html_to_text_strips_arxiv_site_chrome():
    """Site chrome, report dialog, and author-note widgets must not leak."""
    text = _html_to_text(ARXIV_CHROME_FIXTURE)
    assert "Paper body sentence about attention." in text
    assert "Ashish Vaswani" in text
    assert text.count("Attention Is All You Need") == 1
    leaked = [
        "Content selection saved",
        "Describe the issue below",
        "arXiv is now an independent nonprofit",
        "Learn more",
        "Title:",
        "Description:",
        "Thanks:",
        "ORCID",
        "Affiliation:",
        "Email:",
        "Report GitHub Issue",
        "Back to arXiv",
        "Why HTML?",
        "arXiv:1706.03762",
    ]
    for snippet in leaked:
        assert snippet not in text, snippet


def test_html_to_text_keeps_math_once():
    """MathML + TeX annotation must not be concatenated as 0.44 0.44."""
    text = _html_to_text(ARXIV_MATH_FIXTURE)
    assert "measured decode is" in text
    assert "tok/s warm" in text
    assert text.count("0.44") == 1
    assert "2.0\\times" in text
    assert text.count("2.0\\times") == 1
    assert "2.0\u00d7" not in text


def _stamp(storage, paper_id, content):
    (storage / f"{paper_id}.md").write_text(content, encoding="utf-8")
    (storage / f"{paper_id}.meta.json").write_text(
        json.dumps({"id": paper_id, "extractor_version": EXTRACTOR_VERSION}),
        encoding="utf-8",
    )


def _patch_path(mocker, storage):
    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path",
        side_effect=lambda pid, suffix=".md": storage / f"{pid}{suffix}",
    )


@pytest.mark.asyncio
async def test_cached_paper_returns_immediately(temp_storage_path, mocker):
    """A paper already in cache is returned immediately without network calls."""
    paper_id = "2103.12345"
    _patch_path(mocker, temp_storage_path)
    _stamp(temp_storage_path, paper_id, "# Cached Paper\nThis is cached content.")
    mock_httpx = mocker.patch("arxiv_mcp_server.tools.download._fetch_html_content")
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "cache"
    assert "Cached Paper" in result["content"]
    assert result["content_length"] == len("# Cached Paper\nThis is cached content.")
    assert result["next_start"] is None
    assert result["is_truncated"] is False
    mock_httpx.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_download_cache_supports_content_pagination(temp_storage_path, mocker):
    """download_paper can return a bounded chunk to avoid MCP client truncation."""
    paper_id = "2505.13525"
    _patch_path(mocker, temp_storage_path)
    content = "abcdefghijklmnopqrstuvwxyz"
    _stamp(temp_storage_path, paper_id, content)
    mock_httpx = mocker.patch("arxiv_mcp_server.tools.download._fetch_html_content")
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download(
        {"paper_id": paper_id, "start": 10, "max_chars": 5}
    )
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "cache"
    assert result["content_length"] == len(content)
    assert result["start"] == 10
    assert result["returned_chars"] == 5
    assert result["next_start"] == 15
    assert result["is_truncated"] is True
    chunk = result["content"].split("\n\n", 1)[1]
    assert chunk == "klmno"
    mock_httpx.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_html_endpoint_success(temp_storage_path, mocker):
    """HTML endpoint returns 200 -> content saved and returned directly."""
    paper_id = "2103.11111"
    _patch_path(mocker, temp_storage_path)
    html_text = "Title of the Paper\nAbstract content goes here."
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content", return_value=html_text
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_arxiv_metadata", return_value=None
    )
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "html"
    assert result["content"].endswith(html_text)
    assert result["content"].startswith("[UNTRUSTED EXTERNAL CONTENT")
    assert (temp_storage_path / f"{paper_id}.md").exists()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_html_404_falls_back_to_pdf(temp_storage_path, mocker):
    """HTML endpoint returns None (404) -> falls back to PDF conversion."""
    paper_id = "2103.22222"
    _patch_path(mocker, temp_storage_path)
    mocker.patch("arxiv_mcp_server.tools.download._pdf_available", True)
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content", return_value=None
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
    paper_id = "9999.99999"
    _patch_path(mocker, temp_storage_path)
    mocker.patch("arxiv_mcp_server.tools.download._pdf_available", True)
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content", return_value=None
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_pdf_content",
        side_effect=PaperNotFoundError(f"Paper {paper_id} not found on arXiv"),
    )

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "not found on arXiv" in result["message"]


@pytest.mark.asyncio
async def test_html_download_persists_local_metadata(temp_storage_path, mocker):
    """HTML downloads should write a sidecar so list_papers needs no re-fetch."""
    paper_id = "2103.55555"
    _patch_path(mocker, temp_storage_path)
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value="Attention Is All You Need\nAbstract body.",
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_arxiv_metadata",
        return_value={
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani"],
            "published": "2017-06-12T00:00:00+00:00",
        },
    )
    mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)
    assert result["status"] == "success"

    sidecar = json.loads(
        (temp_storage_path / f"{paper_id}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["id"] == paper_id
    assert sidecar["title"] == "Attention Is All You Need"
    assert sidecar["authors"] == ["Ashish Vaswani"]
    assert sidecar["published"] == "2017-06-12T00:00:00+00:00"
    assert sidecar["extractor_version"] == EXTRACTOR_VERSION


@pytest.mark.asyncio
async def test_pdf_download_persists_metadata_from_arxiv_result(
    temp_storage_path, mocker, mock_paper
):
    """PDF fallback already has an arXiv result — persist it locally."""
    paper_id = "2103.66666"
    _patch_path(mocker, temp_storage_path)
    mocker.patch("arxiv_mcp_server.tools.download._pdf_available", True)
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content", return_value=None
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_pdf_content",
        return_value=("# Test Paper\nConverted from PDF.", mock_paper),
    )
    fetch_meta = mocker.patch("arxiv_mcp_server.tools.download._fetch_arxiv_metadata")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)
    assert result["status"] == "success"
    fetch_meta.assert_not_called()

    sidecar = json.loads(
        (temp_storage_path / f"{paper_id}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["title"] == "Test Paper"
    assert sidecar["authors"] == ["John Doe", "Jane Smith"]
    assert sidecar["published"].startswith("2023-01-01")
    assert sidecar["extractor_version"] == EXTRACTOR_VERSION
