"""Tests for paper download functionality (sync HTML-first pipeline)."""

import pytest
import asyncio
import json
from unittest.mock import MagicMock

import arxiv

from arxiv_mcp_server.tools.download import (
    handle_download,
    get_paper_path,
    _html_to_text,
    _fetch_html_content,
    _download_arxiv_pdf_to_path,
    _fetch_pdf_content,
    PaperNotFoundError,
)

# ---------------------------------------------------------------------------
# PDF download helper (httpx streaming)
# ---------------------------------------------------------------------------


def test_download_arxiv_pdf_streams_via_httpx(temp_storage_path, mocker):
    """PDF streaming uses a canonical URL without relying on removed v4 attributes."""
    import arxiv_mcp_server.arxiv_api as api

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

    mocker.patch.object(api.httpx, "Client", return_value=http_client)

    class Arxiv4Result:
        def get_short_id(self):
            return "2103.00000v2"

    dest = temp_storage_path / "paper.pdf"
    _download_arxiv_pdf_to_path(Arxiv4Result(), dest)

    assert dest.read_bytes() == b"chunk-onechunk-two"
    http_client.stream.assert_called_once_with(
        "GET", "https://arxiv.org/pdf/2103.00000v2.pdf"
    )


def test_download_arxiv_pdf_supports_legacy_ids(temp_storage_path, mocker):
    """Canonical URLs retain legacy category-based arXiv IDs."""
    import arxiv_mcp_server.arxiv_api as api

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.iter_bytes.return_value = [b"pdf"]
    response_context = MagicMock()
    response_context.__enter__.return_value = response
    response_context.__exit__.return_value = False
    client = MagicMock()
    client.stream.return_value = response_context
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    mocker.patch.object(api.httpx, "Client", return_value=client)

    class LegacyResult:
        def get_short_id(self):
            return "hep-th/9901001v3"

    _download_arxiv_pdf_to_path(LegacyResult(), temp_storage_path / "legacy.pdf")

    client.stream.assert_called_once_with(
        "GET", "https://arxiv.org/pdf/hep-th/9901001v3.pdf"
    )


def test_download_arxiv_pdf_removes_partial_file_on_stream_failure(
    temp_storage_path, mocker
):
    """Failed downloads never leave a destination or staging file behind."""
    import arxiv_mcp_server.arxiv_api as api

    def chunks():
        yield b"partial"
        raise RuntimeError("connection lost")

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.iter_bytes.return_value = chunks()
    response_context = MagicMock()
    response_context.__enter__.return_value = response
    response_context.__exit__.return_value = False
    client = MagicMock()
    client.stream.return_value = response_context
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    mocker.patch.object(api.httpx, "Client", return_value=client)

    class Result:
        def get_short_id(self):
            return "2401.00001"

    destination = temp_storage_path / "paper.pdf"
    with pytest.raises(RuntimeError, match="connection lost"):
        _download_arxiv_pdf_to_path(Result(), destination)

    assert not destination.exists()
    assert not destination.with_suffix(".pdf.part").exists()


def test_pdf_conversion_failure_removes_downloaded_pdf(temp_storage_path, mocker):
    """A converter exception must not retain a complete temporary PDF."""
    from arxiv_mcp_server.tools import download as download_module

    paper = MagicMock(spec=arxiv.Result)
    client = MagicMock()
    client.results.return_value = iter([paper])
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)
    mocker.patch.object(download_module, "get_arxiv_client", return_value=client)
    mocker.patch.object(
        download_module.ARXIV_RATE_LIMITER,
        "run_sync",
        side_effect=lambda operation: operation(),
    )
    pdf_path = temp_storage_path / "2401.00001.pdf"
    mocker.patch.object(download_module, "get_paper_path", return_value=pdf_path)
    mocker.patch.object(
        download_module,
        "_download_arxiv_pdf_to_path",
        side_effect=lambda _paper, destination: destination.write_bytes(b"pdf"),
    )
    converter = MagicMock()
    converter.to_markdown.side_effect = RuntimeError("conversion failed")
    mocker.patch.object(download_module, "pymupdf4llm", converter)

    with pytest.raises(RuntimeError, match="conversion failed"):
        _fetch_pdf_content("2401.00001")

    assert not pdf_path.exists()


@pytest.mark.asyncio
async def test_index_task_not_created_without_semantic_dependencies(mocker):
    """Missing pro dependencies must not create orphan background tasks."""
    from arxiv_mcp_server.tools import download as download_module

    create_task = mocker.patch.object(asyncio, "create_task")
    mocker.patch.object(
        download_module, "_semantic_dependencies_available", return_value=False
    )

    download_module._track_index_task(download_module._run_index_by_id("2401.00001"))

    create_task.assert_not_called()
    assert not download_module._index_tasks


@pytest.mark.asyncio
async def test_shutdown_waits_for_running_index_worker(mocker):
    """Shutdown must not return while a to_thread indexing worker is running."""
    import threading

    from arxiv_mcp_server.tools import download as download_module

    worker_started = threading.Event()
    release_worker = threading.Event()

    def worker():
        worker_started.set()
        release_worker.wait(timeout=5)

    async def threaded_index():
        await asyncio.to_thread(worker)

    mocker.patch.object(
        download_module, "_semantic_dependencies_available", return_value=True
    )
    download_module._track_index_task(threaded_index())
    await asyncio.to_thread(worker_started.wait, 1)

    shutdown = asyncio.create_task(download_module.shutdown_background_tasks())
    await asyncio.sleep(0.02)
    returned_while_worker_running = shutdown.done()
    release_worker.set()
    await shutdown

    assert not returned_while_worker_running
    assert not download_module._index_tasks
    assert download_module._index_semaphore is None


def test_same_paper_pdf_conversions_are_serialized(mocker):
    """Concurrent requests for one paper cannot share/delete the same PDF."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from arxiv_mcp_server.tools import download as download_module

    active = 0
    max_active = 0
    guard = threading.Lock()
    both_requested = threading.Barrier(2)

    def conversion(_paper_id):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        threading.Event().wait(0.03)
        with guard:
            active -= 1
        return "markdown", object()

    def request():
        both_requested.wait(timeout=2)
        return download_module._fetch_pdf_content("2401.00001")

    mocker.patch.object(download_module, "_fetch_pdf_content_unlocked", conversion)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        for future in futures:
            future.result(timeout=3)

    assert max_active == 1


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
    assert result["content_length"] == len("# Cached Paper\nThis is cached content.")
    assert result["next_start"] is None
    assert result["is_truncated"] is False
    mock_httpx.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_download_cache_supports_content_pagination(temp_storage_path, mocker):
    """download_paper can return a bounded chunk to avoid MCP client truncation."""
    paper_id = "2505.13525"

    def fake_path(pid, suffix=".md"):
        return temp_storage_path / f"{pid}{suffix}"

    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path", side_effect=fake_path
    )

    md_path = temp_storage_path / f"{paper_id}.md"
    content = "abcdefghijklmnopqrstuvwxyz"
    md_path.write_text(content, encoding="utf-8")
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
    paper_id = "9999.99999"

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
async def test_download_rejects_path_traversal_paper_id(temp_storage_path, mocker):
    """Paper IDs cannot escape the configured storage directory."""
    mocker.patch.object(
        __import__("arxiv_mcp_server.tools.download", fromlist=["settings"]).settings,
        "_get_storage_path_from_args",
        return_value=temp_storage_path,
    )

    response = await handle_download({"paper_id": "../../private/secret"})
    payload = json.loads(response[0].text)

    assert payload["status"] == "error"
    assert "invalid arxiv id" in payload["message"].lower()


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
