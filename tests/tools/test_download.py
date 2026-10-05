"""Tests for paper download functionality (sync HTML-first pipeline)."""

import pytest
import asyncio
import json
from unittest.mock import MagicMock

import arxiv

from arxiv_mcp_server.arxiv_api import ArxivTimeoutError
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


@pytest.fixture
def zero_rate_limit():
    """Zero out the arXiv rate limiter's min_interval for fast tests."""
    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    original_interval = ARXIV_RATE_LIMITER.min_interval
    ARXIV_RATE_LIMITER.min_interval = 0.0
    yield
    ARXIV_RATE_LIMITER.min_interval = original_interval


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
    stream_response.iter_raw.return_value = [b"chunk-one", b"chunk-two"]

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
    # Provide deadline parameter (None means fresh budget)
    _download_arxiv_pdf_to_path(Arxiv4Result(), dest, deadline=None)

    assert dest.read_bytes() == b"chunk-onechunk-two"
    http_client.stream.assert_called_once_with(
        "GET", "https://arxiv.org/pdf/2103.00000v2.pdf"
    )


def test_download_arxiv_pdf_supports_legacy_ids(temp_storage_path, mocker):
    """Canonical URLs retain legacy category-based arXiv IDs."""
    import arxiv_mcp_server.arxiv_api as api

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.iter_raw.return_value = [b"pdf"]
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

    _download_arxiv_pdf_to_path(
        LegacyResult(), temp_storage_path / "legacy.pdf", deadline=None
    )

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
    response.iter_raw.return_value = chunks()
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
        _download_arxiv_pdf_to_path(Result(), destination, deadline=None)

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
        side_effect=lambda _paper, destination, deadline: destination.write_bytes(
            b"pdf"
        ),
    )
    converter = MagicMock()
    converter.to_markdown.side_effect = RuntimeError("conversion failed")
    mocker.patch.object(download_module, "pymupdf4llm", converter)

    with pytest.raises(RuntimeError, match="conversion failed"):
        # _fetch_pdf_content now requires a deadline parameter
        import time

        deadline = time.monotonic() + 50.0
        _fetch_pdf_content("2401.00001", deadline)

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


@pytest.mark.asyncio
async def test_metadata_honors_remaining_deadline_after_html(mocker, temp_storage_path):
    """Regression #284 bug 2: metadata fetch checks deadline and skips when exhausted.

    Test fails without the fix: metadata lookup doesn't check the deadline parameter.
    No real sleeps; verifies the deadline was passed and checked.
    """
    import time
    from arxiv_mcp_server.tools import download as download_module

    mocker.patch.object(
        download_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )

    # Mock configuration: tight budget to force skip
    test_settings = MagicMock()
    test_settings.STORAGE_PATH = temp_storage_path
    test_settings.ARXIV_MAX_TOTAL_TIME = 2
    test_settings.ARXIV_REQUEST_TIMEOUT = 1
    test_settings.ARXIV_CONNECT_TIMEOUT = 10
    test_settings.ARXIV_MAX_RETRIES = 0
    test_settings.get_request_timeout = lambda: 1
    mocker.patch.object(download_module, "settings", test_settings)

    # Mock rate limiter: 1.5s wait (forcing metadata skip)
    mocker.patch.object(
        download_module.ARXIV_RATE_LIMITER,
        "seconds_until_next_slot",
        return_value=1.5,
    )
    mocker.patch.object(
        download_module.ARXIV_RATE_LIMITER,
        "run_sync",
        side_effect=lambda op: op(),
    )

    # Mock HTML fetch: immediate success
    mocker.patch.object(
        download_module, "_fetch_html_content", return_value="# Paper\n\nContent here."
    )

    # Mock get_arxiv_client to avoid real API calls
    mock_client = MagicMock()
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    # Track whether deadline was passed to metadata fetch
    metadata_args = {"deadline": None}

    real_fetch_metadata = download_module._fetch_arxiv_metadata

    def track_metadata_call(paper_id, deadline=None):
        metadata_args["deadline"] = deadline
        # Call real implementation which should skip
        return real_fetch_metadata(paper_id, deadline)

    mocker.patch.object(
        download_module, "_fetch_arxiv_metadata", side_effect=track_metadata_call
    )

    # Mock cleanup and indexing
    mocker.patch.object(download_module, "_cleanup_versioned_aliases", lambda _: None)

    def close_coroutine(coro):
        """Close coroutine to silence 'never awaited' warning."""
        coro.close()

    mocker.patch.object(download_module, "_track_index_task", close_coroutine)

    response = await handle_download({"paper_id": "2404.19756"})
    result = json.loads(response[0].text)

    # Must succeed even when metadata is skipped
    assert result["status"] == "success"
    assert result["source"] == "html"

    # CRITICAL: deadline must have been passed (fix adds this parameter)
    assert (
        metadata_args["deadline"] is not None
    ), "Deadline parameter was not passed to metadata fetch"


@pytest.mark.asyncio
async def test_metadata_skipped_gracefully_when_budget_exhausted(
    mocker, temp_storage_path
):
    """Regression #284 bug 2: budget check prevents metadata when time is insufficient.

    Test fails without the fix: metadata is attempted regardless of remaining budget.
    Verifies that when deadline + rate limiter wait exceeds budget, metadata returns None.
    """
    import time
    from arxiv_mcp_server.tools import download as download_module

    mocker.patch.object(
        download_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )

    test_settings = MagicMock()
    test_settings.STORAGE_PATH = temp_storage_path
    test_settings.ARXIV_MAX_TOTAL_TIME = 3
    test_settings.ARXIV_REQUEST_TIMEOUT = 1
    test_settings.ARXIV_CONNECT_TIMEOUT = 10
    test_settings.ARXIV_MAX_RETRIES = 0
    test_settings.get_request_timeout = lambda: 1
    mocker.patch.object(download_module, "settings", test_settings)

    # Rate limiter says 2.5s wait needed; with 3s total budget and 1s min_attempt_time,
    # metadata should be skipped (need 3.5s, have 3s)
    mocker.patch.object(
        download_module.ARXIV_RATE_LIMITER,
        "seconds_until_next_slot",
        return_value=2.5,
    )
    mocker.patch.object(
        download_module.ARXIV_RATE_LIMITER,
        "run_sync",
        side_effect=lambda op: op(),
    )

    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Paper\n\nContent here.",
    )

    # Track whether metadata tried to call arxiv API (it shouldn't)
    api_called = {"value": False}

    def mock_get_client(num_retries=None):
        api_called["value"] = True
        raise AssertionError("Metadata should have been skipped, not call arxiv API")

    mocker.patch.object(
        download_module, "get_arxiv_client", side_effect=mock_get_client
    )
    mocker.patch.object(download_module, "_cleanup_versioned_aliases", lambda _: None)

    def close_coroutine(coro):
        """Close coroutine to silence 'never awaited' warning."""
        coro.close()

    mocker.patch.object(download_module, "_track_index_task", close_coroutine)

    response = await handle_download({"paper_id": "2404.19756"})
    result = json.loads(response[0].text)

    # Should succeed with HTML content, metadata skipped
    assert result["status"] == "success"
    assert result["source"] == "html"
    assert "Content here" in result["content"]
    # CRITICAL: API must not have been called (fix prevents this)
    assert not api_called[
        "value"
    ], "Metadata should have been skipped due to insufficient budget"


def test_same_paper_pdf_conversions_are_serialized(mocker):
    """Concurrent requests for one paper cannot share/delete the same PDF."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from arxiv_mcp_server.tools import download as download_module

    active = 0
    max_active = 0
    guard = threading.Lock()
    both_requested = threading.Barrier(2)

    def conversion(_paper_id, _deadline):
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
        # _fetch_pdf_content now requires a deadline parameter
        import time

        deadline = time.monotonic() + 50.0
        return download_module._fetch_pdf_content("2401.00001", deadline)

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
# Shared ID normalization (issue #200)
# ---------------------------------------------------------------------------

_ID_FORM_CASES = [
    ("1810.04805", "1810.04805"),
    ("arxiv:1810.04805", "1810.04805"),
    ("https://arxiv.org/abs/1810.04805", "1810.04805"),
    ("https://arxiv.org/pdf/1810.04805.pdf", "1810.04805"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_id, expected", _ID_FORM_CASES)
async def test_download_paper_id_normalize_matrix(
    temp_storage_path, mocker, raw_id, expected
):
    """download_paper accepts bare / arxiv: / abs / pdf forms via parse_arxiv_id."""
    from arxiv_mcp_server.tools import download as download_module

    _write_cached_paper(temp_storage_path, expected, "# Cached\nNormalized ID path.")
    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mock_html = mocker.patch.object(download_module, "_fetch_html_content")
    mock_pdf = mocker.patch.object(download_module, "_fetch_pdf_content")

    response = await handle_download({"paper_id": raw_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["paper_id"] == expected
    assert result["source"] == "cache"
    mock_html.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_id",
    ["not-a-paper", "arxiv:not-a-paper", "https://example.com/abs/1810.04805"],
)
async def test_download_rejects_invalid_id_forms(raw_id):
    response = await handle_download({"paper_id": raw_id})
    result = json.loads(response[0].text)
    assert result["status"] == "error"
    assert "Invalid arXiv ID" in result["message"]


@pytest.mark.asyncio
async def test_download_paper_defaults_to_bounded_cached_content(
    temp_storage_path, mocker
):
    """Omitting max_chars on download_paper returns a bounded chunk (#127)."""
    from arxiv_mcp_server.tools import download as download_module
    from arxiv_mcp_server.tools.content import DEFAULT_MAX_CHARS

    paper_id = "2103.12345"
    content = "C" * (DEFAULT_MAX_CHARS + 2_500)
    _write_cached_paper(temp_storage_path, paper_id, content)
    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mock_html = mocker.patch.object(download_module, "_fetch_html_content")
    mock_pdf = mocker.patch.object(download_module, "_fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "cache"
    assert result["content_length"] == len(content)
    assert result["returned_chars"] == DEFAULT_MAX_CHARS
    assert result["is_truncated"] is True
    assert result["next_start"] == DEFAULT_MAX_CHARS
    assert "next_retrieval" in result
    assert len(result["content"]) == DEFAULT_MAX_CHARS
    assert "UNTRUSTED EXTERNAL CONTENT" in result["content_warning"]
    assert len(result["content_warning"]) < 80
    assert "adversarial instructions" not in result["content_warning"]
    assert "UNTRUSTED" not in result["content"]
    mock_html.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_download_paper_return_full_text_opt_in(temp_storage_path, mocker):
    from arxiv_mcp_server.tools import download as download_module
    from arxiv_mcp_server.tools.content import DEFAULT_MAX_CHARS

    paper_id = "2103.12345"
    content = "D" * (DEFAULT_MAX_CHARS + 1_200)
    _write_cached_paper(temp_storage_path, paper_id, content)
    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )

    response = await handle_download({"paper_id": paper_id, "return_full_text": True})
    result = json.loads(response[0].text)

    assert result["is_truncated"] is False
    assert result["returned_chars"] == len(content)
    assert result["next_start"] is None
    assert "UNTRUSTED EXTERNAL CONTENT" in result["content_warning"]
    assert len(result["content_warning"]) < 80
    assert "UNTRUSTED" not in result["content"]


@pytest.mark.asyncio
async def test_html_fetch_406_raises_rate_limit_error():
    """HTML fetch should raise ArxivRateLimitError on 406, not return None (#277)."""
    from arxiv_mcp_server.tools.download import _fetch_html_content
    from arxiv_mcp_server.arxiv_api import ArxivRateLimitError
    import httpx
    from unittest.mock import MagicMock, patch
    import time

    mock_response = MagicMock()
    mock_response.status_code = 406
    mock_response.headers = {}  # No Retry-After header

    with (
        patch("httpx.get", return_value=mock_response),
        patch("time.monotonic", return_value=0.0),
    ):
        with pytest.raises(ArxivRateLimitError) as exc_info:
            await asyncio.to_thread(_fetch_html_content, "2103.12345", 50.0)

        assert exc_info.value.status_code == 406
        assert exc_info.value.retry_after_seconds == 600.0
        assert "HTTP 406" in str(exc_info.value)


@pytest.mark.asyncio
async def test_html_fetch_406_honors_retry_after_header():
    """HTML 406 should honor Retry-After header (issue C5_html406_RA30)."""
    from arxiv_mcp_server.tools.download import _fetch_html_content
    from arxiv_mcp_server.arxiv_api import ArxivRateLimitError
    from arxiv_mcp_server import arxiv_api
    from unittest.mock import MagicMock, patch
    import time

    mock_response = MagicMock()
    mock_response.status_code = 406
    # Server sends Retry-After: 30
    mock_response.headers = {"Retry-After": "30"}

    with (
        patch("httpx.get", return_value=mock_response),
        patch("time.monotonic", return_value=0.0),
        patch.object(
            arxiv_api.ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f: f()
        ),
    ):
        with pytest.raises(ArxivRateLimitError) as exc_info:
            await asyncio.to_thread(_fetch_html_content, "2103.12345", 50.0)

        assert exc_info.value.status_code == 406
        # Should use the server's Retry-After value (30), not the default (600)
        assert exc_info.value.retry_after_seconds == 30.0
        assert "HTTP 406" in str(exc_info.value)


@pytest.mark.asyncio
async def test_download_html_406_returns_rate_limited_response(
    temp_storage_path, mocker
):
    """Download should return rate_limited response when HTML fetch gets 406 (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    from arxiv_mcp_server.arxiv_api import ArxivRateLimitError

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        side_effect=ArxivRateLimitError(
            "arXiv is rate limiting this IP (HTTP 406). "
            "Please wait 600 seconds before retrying.",
            status_code=406,
            retry_after_seconds=600.0,
        ),
    )

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "rate_limited"
    assert result["http_status"] == 406
    assert result["retry_after_seconds"] == 600.0
    assert "HTTP 406" in result["message"]


@pytest.mark.asyncio
async def test_download_pdf_406_returns_rate_limited_response(
    temp_storage_path, mocker
):
    """Download should return rate_limited response when PDF fetch gets 406 (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    from arxiv_mcp_server.tools.search import ArxivRateLimitError

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)
    mocker.patch.object(
        download_module,
        "_fetch_pdf_content",
        side_effect=ArxivRateLimitError(
            "arXiv is rate limiting this IP (HTTP 406). "
            "Please wait 600 seconds before retrying.",
            status_code=406,
            retry_after_seconds=600.0,
        ),
    )

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "rate_limited"
    assert result["http_status"] == 406
    assert result["retry_after_seconds"] == 600.0
    assert "HTTP 406" in result["message"]


@pytest.mark.asyncio
async def test_download_pdf_http_error_no_traceback(temp_storage_path, mocker, caplog):
    """PDF HTTP errors should not log full traceback (#166, #277)."""
    from arxiv_mcp_server.tools import download as download_module
    import logging

    caplog.set_level(logging.ERROR)

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)
    mocker.patch.object(
        download_module,
        "_fetch_pdf_content",
        side_effect=RuntimeError("arXiv PDF download HTTP error (HTTP 500)"),
    )

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "arXiv PDF download HTTP error (HTTP 500)" in result["message"]
    assert "export.arxiv.org" not in result["message"]
    # Should log error, not exception (no traceback)
    assert "Download error for 2103.12345" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_download_existence_check_500_no_url_leak(temp_storage_path, mocker):
    """Existence check HTTP 500 should not leak URL or log traceback (#166, #277)."""
    from arxiv_mcp_server.tools import download as download_module
    from arxiv_mcp_server.tools.search import ArxivRateLimitError
    import httpx
    import logging

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)

    # Mock _rate_limited_get to raise HTTPStatusError for 500
    request = httpx.Request(
        "GET", "https://export.arxiv.org/api/query?id_list=2103.12345"
    )
    response = httpx.Response(500, request=request)
    mocker.patch.object(
        download_module,
        "_rate_limited_get",
        side_effect=httpx.HTTPStatusError(
            "Server error '500 Internal Server Error' for url 'https://export.arxiv.org/api/query?id_list=2103.12345'",
            request=request,
            response=response,
        ),
    )

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "arXiv API HTTP error (HTTP 500)" in result["message"]
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]
    assert "export.arxiv.org" not in result["message"]


@pytest.mark.asyncio
async def test_download_pdf_metadata_lookup_406_no_url_leak(
    temp_storage_path, mocker, zero_rate_limit
):
    """PDF metadata lookup 406 should be rate_limited, not leak URL (#166, #277)."""
    from arxiv_mcp_server.tools import download as download_module
    from arxiv_mcp_server.tools.search import ArxivRateLimitError
    from arxiv_mcp_server import arxiv_api
    import arxiv
    import httpx

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)
    mocker.patch.object(
        arxiv_api.ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f: f()
    )

    # Mock get_arxiv_client to return a client that raises HTTPError with status 406
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = arxiv.HTTPError(
        "https://export.arxiv.org/api/query?id_list=2103.12345",
        0,
        406,
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "rate_limited"
    assert result["http_status"] == 406
    assert result["retry_after_seconds"] == 600.0
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]
    assert "export.arxiv.org" not in result["message"]


@pytest.mark.asyncio
async def test_download_pdf_metadata_lookup_500_no_url_leak(
    temp_storage_path, mocker, zero_rate_limit
):
    """PDF metadata lookup 500 should not leak URL (#166, #277)."""
    from arxiv_mcp_server.tools import download as download_module
    import arxiv

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises HTTPError with status 500
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = arxiv.HTTPError(
        "https://export.arxiv.org/api/query?id_list=2103.12345",
        0,
        500,
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "arXiv API HTTP error (HTTP 500)" in result["message"]
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]
    assert "export.arxiv.org" not in result["message"]


@pytest.mark.asyncio
async def test_pdf_metadata_500_on_2406_id_not_406_rate_limit(
    temp_storage_path, mocker
):
    """HTTP 500 on paper 2406.xxxxx must NOT be reported as 406 rate limit (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import arxiv
    import httpx

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises HTTPError with status 500
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = arxiv.HTTPError(
        "https://export.arxiv.org/api/query?id_list=2406.12345",
        0,
        500,
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2406.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "arXiv API HTTP error (HTTP 500)" in result["message"]
    # Must NOT be reported as 406 rate limit
    assert result["status"] != "rate_limited"
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]


@pytest.mark.asyncio
async def test_pdf_metadata_500_on_2503_id_not_503_rate_limit(
    temp_storage_path, mocker
):
    """HTTP 500 on paper 2503.xxxxx must NOT be reported as 503 rate limit (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import arxiv
    import httpx

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises HTTPError with status 500
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = arxiv.HTTPError(
        "https://export.arxiv.org/api/query?id_list=2503.01234",
        0,
        500,
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2503.01234"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "arXiv API HTTP error (HTTP 500)" in result["message"]
    # Must NOT be reported as 503 rate limit
    assert result["status"] != "rate_limited"
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]


@pytest.mark.asyncio
async def test_pdf_metadata_502_on_2406_id_not_406_rate_limit(
    temp_storage_path, mocker
):
    """HTTP 502 on paper 2406.xxxxx must NOT be reported as 406 rate limit (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import arxiv

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises HTTPError with status 502
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = arxiv.HTTPError(
        "https://export.arxiv.org/api/query?id_list=2406.12345",
        0,
        502,
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2406.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "arXiv API HTTP error (HTTP 502)" in result["message"]
    # Must NOT be reported as 406 rate limit
    assert result["status"] != "rate_limited"
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]


@pytest.mark.asyncio
async def test_pdf_metadata_502_on_2503_id_not_503_rate_limit(
    temp_storage_path, mocker
):
    """HTTP 502 on paper 2503.xxxxx must NOT be reported as 503 rate limit (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import arxiv

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises HTTPError with status 502
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = arxiv.HTTPError(
        "https://export.arxiv.org/api/query?id_list=2503.01234",
        0,
        502,
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2503.01234"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "arXiv API HTTP error (HTTP 502)" in result["message"]
    # Must NOT be reported as 503 rate limit
    assert result["status"] != "rate_limited"
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]


@pytest.mark.asyncio
async def test_pdf_metadata_connection_error_on_2406_id(
    temp_storage_path, mocker, zero_rate_limit
):
    """Connection error on paper 2406.xxxxx must not be falsely identified (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import requests

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises ConnectionError
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = requests.exceptions.ConnectionError(
        "Connection refused"
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2406.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    # Must NOT be reported as rate limit
    assert result["status"] != "rate_limited"
    # Should mention network error
    assert (
        "network" in result["message"].lower() or "reach" in result["message"].lower()
    )
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]


@pytest.mark.asyncio
async def test_pdf_metadata_connection_error_on_2503_id(
    temp_storage_path, mocker, zero_rate_limit
):
    """Connection error on paper 2503.xxxxx must not be falsely identified (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import requests

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises ConnectionError
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = requests.exceptions.ConnectionError(
        "Connection refused"
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2503.01234"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    # Must NOT be reported as rate limit
    assert result["status"] != "rate_limited"
    # Should mention network error
    assert (
        "network" in result["message"].lower() or "reach" in result["message"].lower()
    )
    # No URLs should appear
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]


@pytest.mark.asyncio
async def test_pdf_metadata_406_minimal_retries(
    temp_storage_path, mocker, zero_rate_limit
):
    """PDF metadata lookup on 406 should use minimal retries (1 retry = 2 total) (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import arxiv

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to track call count
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = arxiv.HTTPError(
        "https://export.arxiv.org/api/query?id_list=2103.12345",
        0,
        406,
    )
    mock_get_client = mocker.patch.object(
        download_module, "get_arxiv_client", return_value=mock_client
    )

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "rate_limited"
    assert result["http_status"] == 406
    # Verify get_arxiv_client was called with num_retries=0
    mock_get_client.assert_called_once_with(num_retries=0)


@pytest.mark.asyncio
async def test_pdf_metadata_network_error_clean_message(
    temp_storage_path, mocker, zero_rate_limit
):
    """Network errors during PDF metadata lookup should report cleanly without URL or traceback (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import requests

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises ConnectionError
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = requests.exceptions.ConnectionError(
        "Connection refused for https://export.arxiv.org/api/query?id_list=2103.12345"
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    # Should mention network error but not URLs
    assert (
        "network" in result["message"].lower() or "reach" in result["message"].lower()
    )
    assert "https://" not in result["message"]
    assert "http://" not in result["message"]
    assert "export.arxiv.org" not in result["message"]


@pytest.mark.asyncio
async def test_pdf_metadata_network_error_no_traceback_or_url_in_logs(
    temp_storage_path, mocker, caplog, zero_rate_limit
):
    """Network errors during PDF metadata lookup should not log traceback or URL (#277)."""
    from arxiv_mcp_server.tools import download as download_module
    import requests
    import logging

    caplog.set_level(logging.ERROR)

    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": temp_storage_path / f"{pid}{suffix}",
    )
    mocker.patch.object(download_module, "_fetch_html_content", return_value=None)
    mocker.patch.object(download_module, "_paper_exists_on_arxiv", return_value=True)
    mocker.patch.object(download_module, "_load_pdf_dependencies", return_value=True)

    # Mock get_arxiv_client to return a client that raises ConnectionError with URL
    mock_client = mocker.MagicMock()
    mock_client.results.side_effect = requests.exceptions.ConnectionError(
        "Connection refused for https://export.arxiv.org/api/query?id_list=2103.12345"
    )
    mocker.patch.object(download_module, "get_arxiv_client", return_value=mock_client)

    response = await handle_download({"paper_id": "2103.12345"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    # Check logs: should have error log but no traceback
    assert "Download error for 2103.12345" in caplog.text
    assert "Traceback" not in caplog.text
    # No URL should appear in logs
    assert "export.arxiv.org" not in caplog.text
    assert "https://" not in caplog.text
