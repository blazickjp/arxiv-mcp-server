"""Tests for paper download functionality (sync HTML-first pipeline)."""

import pytest
import asyncio
import json
from unittest.mock import MagicMock

import arxiv

from arxiv_mcp_server.tools.download import (
    EXTRACTOR_VERSION,
    handle_download,
    get_paper_path,
    _html_to_text,
    _fetch_html_content,
    _download_arxiv_pdf_to_path,
    _fetch_pdf_content,
    PaperNotFoundError,
    download_tool,
)


def _write_cached_paper(storage, paper_id, content, extractor_version=None):
    """Write markdown plus a sidecar stamped with an extractor version."""
    (storage / f"{paper_id}.md").write_text(content, encoding="utf-8")
    version = EXTRACTOR_VERSION if extractor_version is None else extractor_version
    payload = {
        "id": paper_id,
        "title": "Cached Paper",
        "authors": [],
        "published": None,
        "extractor_version": version,
    }
    (storage / f"{paper_id}.meta.json").write_text(
        json.dumps(payload), encoding="utf-8"
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
        <math class="ltx_Math" alttext="2.0\\times" display="inline">
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
