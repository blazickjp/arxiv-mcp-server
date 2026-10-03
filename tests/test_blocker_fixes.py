"""Tests for blocker fixes identified in pre-merge testing."""

import asyncio
import itertools
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arxiv_mcp_server.arxiv_api import (
    retry_with_backoff,
    ArxivRateLimitError,
    ArxivTimeoutError,
)


class TestBlockerFixes:
    """Tests verifying fixes for all blockers from pre-merge testing report."""

    @pytest.mark.asyncio
    async def test_trickling_body_cut_at_budget(self):
        """Blocker 1: Trickling response body is cut off by budget enforcement."""
        call_count = 0

        async def trickling_response():
            nonlocal call_count
            call_count += 1
            # Simulate slow response that would exceed budget
            await asyncio.sleep(0.6)  # Longer than 0.5s budget
            return "success"

        start = time.monotonic()
        with pytest.raises(ArxivTimeoutError) as exc_info:
            await retry_with_backoff(
                trickling_response,
                max_retries=5,
                initial_backoff=0.01,
                max_backoff=0.1,
                max_total_time=0.5,
                operation_name="trickling test",
            )
        elapsed = time.monotonic() - start

        # Should timeout before completing the slow operation
        assert elapsed < 1.0
        assert call_count >= 1
        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_concurrent_calls_own_budgets(self):
        """Blocker 6: Concurrent calls each respect their own budget, not serialized."""

        async def stalling_operation():
            await asyncio.sleep(0.3)
            raise httpx.TimeoutException("stall")

        start = time.monotonic()
        # Launch two concurrent calls, each with 0.5s budget
        results = await asyncio.gather(
            retry_with_backoff(
                stalling_operation,
                max_retries=10,
                initial_backoff=0.01,
                max_backoff=0.1,
                max_total_time=0.5,
                operation_name="call1",
            ),
            retry_with_backoff(
                stalling_operation,
                max_retries=10,
                initial_backoff=0.01,
                max_backoff=0.1,
                max_total_time=0.5,
                operation_name="call2",
            ),
            return_exceptions=True,
        )
        elapsed = time.monotonic() - start

        # Both should fail with timeout
        assert all(isinstance(r, ArxivTimeoutError) for r in results)
        # Should complete in parallel (~0.5s each), not serial (~1.0s)
        assert elapsed < 0.9

    @pytest.mark.asyncio
    async def test_html_timeout_fallback_to_pdf(self):
        """Blocker 7: HTML timeout returns None to allow PDF fallback."""
        from arxiv_mcp_server.tools.download import _fetch_html_content_single_attempt

        with patch.object(httpx, "get") as mock_get:
            mock_get.side_effect = httpx.TimeoutException("timeout")

            # Single attempt should raise TimeoutException
            with pytest.raises(httpx.TimeoutException):
                _fetch_html_content_single_attempt("2103.14030", 30.0)

    @pytest.mark.asyncio
    async def test_pdf_timeout_honest_error(self):
        """Blocker 8: PDF timeout returns ArxivTimeoutError (honest timeout), not rate_limited."""
        from arxiv_mcp_server.arxiv_api import stream_pdf_to_path
        from pathlib import Path
        import tempfile
        import itertools

        # Create mock paper
        mock_paper = MagicMock()
        mock_paper.get_short_id.return_value = "2103.14030"

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"

            # Mock time module to avoid real sleeps
            with (
                patch("time.monotonic") as mock_monotonic,
                patch("time.sleep") as mock_sleep,
            ):
                # Use infinite counter for monotonic time (0.1s increments)
                mock_monotonic.side_effect = (x * 0.1 for x in itertools.count())

                with patch.object(httpx, "Client") as mock_client_cls:
                    mock_client = MagicMock()
                    mock_stream = MagicMock()
                    mock_stream.__enter__ = MagicMock(
                        side_effect=httpx.TimeoutException("timeout")
                    )
                    mock_stream.__exit__ = MagicMock(return_value=False)
                    mock_client.stream.return_value = mock_stream
                    mock_client.__enter__ = MagicMock(return_value=mock_client)
                    mock_client.__exit__ = MagicMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    # Should raise ArxivTimeoutError (honest timeout), not ArxivRateLimitError
                    with pytest.raises(ArxivTimeoutError) as exc_info:
                        stream_pdf_to_path(
                            mock_paper,
                            pdf_path,
                            request_timeout=30.0,
                            user_agent="test",
                        )
                    assert "timed out" in str(exc_info.value).lower()
                    assert "rate" not in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_budget_expiry_message_non_empty_url_free(self):
        """Blocker 2 & 3: Budget expiry returns non-empty, URL-free message."""

        async def always_timeout():
            raise httpx.TimeoutException("timeout")

        with pytest.raises(ArxivTimeoutError) as exc_info:
            await retry_with_backoff(
                always_timeout,
                max_retries=10,
                initial_backoff=0.01,
                max_backoff=0.1,
                max_total_time=0.3,
                operation_name="test operation",
            )

        error_msg = str(exc_info.value)
        assert len(error_msg) > 0
        assert "http://" not in error_msg.lower()
        assert "https://" not in error_msg.lower()
        assert "timed out" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_retry_after_exceeds_budget_immediate_return(self):
        """Blocker 4: Retry-After longer than remaining budget returns immediately."""
        request = httpx.Request("GET", "https://example.com")
        response_429 = httpx.Response(
            429, request=request, headers={"Retry-After": "60"}
        )

        mock_op = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "429", request=request, response=response_429
            )
        )

        start = time.monotonic()
        with pytest.raises(ArxivRateLimitError) as exc_info:
            await retry_with_backoff(
                mock_op,
                max_retries=10,
                initial_backoff=1.0,
                max_backoff=10.0,
                max_total_time=5.0,  # Only 5s budget, but Retry-After is 60s
                operation_name="test op",
            )
        elapsed = time.monotonic() - start

        # Should return immediately (< 1s), not wait for 60s Retry-After
        assert elapsed < 2.0
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after_seconds == 60.0

    @pytest.mark.asyncio
    async def test_http_date_retry_after_html_path(self):
        """Blocker 9: HTTP-date Retry-After parsing on HTML path (returns None for PDF fallback)."""
        from arxiv_mcp_server.tools.download import _fetch_html_content_single_attempt
        from email.utils import formatdate
        import datetime

        # Create HTTP-date for a specific time in the future
        future_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=120
        )
        http_date = formatdate(timeval=future_dt.timestamp(), usegmt=True)

        # Create response with HTTP-date Retry-After
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": http_date}

        with patch.object(httpx, "get", return_value=mock_response):
            # HTML 429 should return None to allow PDF fallback (not raise)
            # The important part is it doesn't crash parsing the HTTP-date
            result = _fetch_html_content_single_attempt("2103.14030", 30.0)
            assert result is None

    @pytest.mark.asyncio
    async def test_http_date_retry_after_pdf_path(self):
        """PDF path should parse HTTP-date Retry-After without ValueError."""
        from arxiv_mcp_server.arxiv_api import stream_pdf_to_path
        from pathlib import Path
        import tempfile
        from email.utils import formatdate
        import datetime
        import itertools

        # Create HTTP-date for a specific time in the future
        future_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=90
        )
        http_date = formatdate(timeval=future_dt.timestamp(), usegmt=True)

        mock_paper = MagicMock()
        mock_paper.get_short_id.return_value = "2103.14030"

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"

            # Mock HTTP response with HTTP-date Retry-After
            request = httpx.Request("GET", "https://example.com")
            response_429 = httpx.Response(
                429, request=request, headers={"Retry-After": http_date}
            )

            with (
                patch("time.monotonic") as mock_monotonic,
                patch("time.sleep") as mock_sleep,
            ):
                # Use infinite counter for monotonic time
                mock_monotonic.side_effect = (x * 0.1 for x in itertools.count())

                with patch.object(httpx, "Client") as mock_client_cls:
                    mock_client = MagicMock()
                    mock_stream = MagicMock()
                    # Mock the stream context manager to raise on __enter__
                    mock_stream.__enter__ = MagicMock(
                        side_effect=httpx.HTTPStatusError(
                            "429", request=request, response=response_429
                        )
                    )
                    mock_stream.__exit__ = MagicMock(return_value=False)
                    mock_client.stream.return_value = mock_stream
                    mock_client.__enter__ = MagicMock(return_value=mock_client)
                    mock_client.__exit__ = MagicMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    # Should raise ArxivRateLimitError with parsed HTTP-date
                    with pytest.raises(ArxivRateLimitError) as exc_info:
                        stream_pdf_to_path(
                            mock_paper,
                            pdf_path,
                            request_timeout=30.0,
                            user_agent="test",
                        )

                    assert exc_info.value.status_code == 429
                    # Should have parsed the date successfully (around 90 seconds)
                    assert 70 < exc_info.value.retry_after_seconds < 110


class TestLatexRateLimiting:
    """Tests for LaTeX rate limiting (406/429/503)."""

    @pytest.mark.asyncio
    async def test_latex_406_rate_limited_one_call(self):
        """LaTeX 406 should give rate_limited after 1 call with 600s retry_after."""
        from arxiv_mcp_server.tools.latex import handle_get_paper_latex
        import itertools

        request = httpx.Request("GET", "https://arxiv.org/e-print/2103.14030")
        response_406 = httpx.Response(406, request=request)

        with (
            patch("time.monotonic", side_effect=(x * 0.1 for x in itertools.count())),
            patch("time.sleep") as mock_sleep,
        ):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_stream = MagicMock()
                mock_stream.__enter__ = MagicMock()
                mock_stream.__enter__.return_value.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError(
                        "406", request=request, response=response_406
                    )
                )
                mock_stream.__exit__ = MagicMock(return_value=False)
                mock_client.stream.return_value = mock_stream
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client_cls.return_value = mock_client

                # Should return rate_limited response immediately, not retry
                result = await handle_get_paper_latex({"paper_id": "2103.14030"})
                payload = json.loads(result[0].text)

                assert payload["status"] == "rate_limited"
                assert payload["http_status"] == 406
                assert payload["retry_after_seconds"] == 600.0
                assert "rate limiting" in payload["message"].lower()
                # Should not have retried or slept
                mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_latex_429_rate_limited_one_call(self):
        """LaTeX 429 should give rate_limited after 1 call, honoring Retry-After."""
        from arxiv_mcp_server.tools.latex import handle_get_paper_latex
        from arxiv_mcp_server import arxiv_api
        import itertools

        request = httpx.Request("GET", "https://arxiv.org/e-print/2103.14030")
        response_429 = httpx.Response(
            429, request=request, headers={"Retry-After": "120"}
        )

        with (
            patch("time.monotonic", side_effect=(x * 0.1 for x in itertools.count())),
            patch("time.sleep") as mock_sleep,
            patch.object(
                arxiv_api.ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f: f()
            ),
        ):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_stream = MagicMock()
                mock_stream.__enter__ = MagicMock()
                mock_stream.__enter__.return_value.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError(
                        "429", request=request, response=response_429
                    )
                )
                mock_stream.__exit__ = MagicMock(return_value=False)
                mock_client.stream.return_value = mock_stream
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client_cls.return_value = mock_client

                # Should return rate_limited response immediately, not retry
                result = await handle_get_paper_latex({"paper_id": "2103.14030"})
                payload = json.loads(result[0].text)

                assert payload["status"] == "rate_limited"
                assert payload["http_status"] == 429
                assert payload["retry_after_seconds"] == 120.0
                assert "rate limiting" in payload["message"].lower()
                assert "120 seconds" in payload["message"]
                # Should not have retried or slept
                mock_sleep.assert_not_called()
                # Should only make 1 HTTP call
                assert mock_client.stream.call_count == 1

    @pytest.mark.asyncio
    async def test_latex_503_rate_limited_one_call(self):
        """LaTeX 503 should give rate_limited after 1 call with 60s retry_after."""
        from arxiv_mcp_server.tools.latex import handle_get_paper_latex
        from arxiv_mcp_server import arxiv_api
        import itertools

        request = httpx.Request("GET", "https://arxiv.org/e-print/2103.14030")
        response_503 = httpx.Response(503, request=request)

        with (
            patch("time.monotonic", side_effect=(x * 0.1 for x in itertools.count())),
            patch("time.sleep") as mock_sleep,
            patch.object(
                arxiv_api.ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f: f()
            ),
        ):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_stream = MagicMock()
                mock_stream.__enter__ = MagicMock()
                mock_stream.__enter__.return_value.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError(
                        "503", request=request, response=response_503
                    )
                )
                mock_stream.__exit__ = MagicMock(return_value=False)
                mock_client.stream.return_value = mock_stream
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client_cls.return_value = mock_client

                # Should return rate_limited response immediately, not retry
                result = await handle_get_paper_latex({"paper_id": "2103.14030"})
                payload = json.loads(result[0].text)

                assert payload["status"] == "rate_limited"
                assert payload["http_status"] == 503
                assert payload["retry_after_seconds"] == 60.0
                assert "rate limiting" in payload["message"].lower()
                # Should not have retried or slept
                mock_sleep.assert_not_called()
                # Should only make 1 HTTP call
                assert mock_client.stream.call_count == 1


class TestNewBlockerFixes:
    """Tests for new blocker fixes: PDF/LaTeX trickle, HTML+PDF single budget."""

    @pytest.mark.asyncio
    async def test_latex_trickle_bounded_by_budget(self):
        """LaTeX trickling body is cut off by wall-clock deadline."""
        from arxiv_mcp_server.tools.latex_archive import _download_source_archive
        from arxiv_mcp_server.tools.latex_archive import LatexSourceError
        from arxiv_mcp_server import arxiv_api

        # Mock a trickling stream that yields 1 byte at a time indefinitely
        class TricklingStream:
            def __iter__(self):
                return self

            def __next__(self):
                # Yield forever until deadline cuts us off
                return b"x"

        with (
            patch("time.monotonic") as mock_monotonic,
            patch("time.sleep") as mock_sleep,
            patch.object(
                arxiv_api.ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f: f()
            ),
        ):
            # Mock time to progress on each check
            call_count = [-1]  # Start at -1 so first call returns 0.0

            def mock_time():
                call_count[0] += 1
                # Progress 0.2s per call to simulate slow trickle
                # With 1.0s budget, should timeout after ~5 checks
                return call_count[0] * 0.2

            mock_monotonic.side_effect = mock_time

            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_response = MagicMock()
                mock_response.raise_for_status = MagicMock()
                mock_response.headers = {}
                mock_response.iter_raw = MagicMock(return_value=TricklingStream())
                mock_stream = MagicMock()
                mock_stream.__enter__ = MagicMock(return_value=mock_response)
                mock_stream.__exit__ = MagicMock(return_value=False)
                mock_client.stream.return_value = mock_stream
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client_cls.return_value = mock_client

                # Should raise LatexSourceError due to deadline exceeded
                with pytest.raises(LatexSourceError) as exc_info:
                    with patch.dict(
                        "os.environ", {"ARXIV_MAX_TOTAL_TIME": "1.0"}, clear=False
                    ):
                        _download_source_archive("2103.14030")

                assert "exceeded deadline" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_pdf_trickle_bounded_by_budget(self):
        """PDF trickling body is cut off by wall-clock deadline."""
        from arxiv_mcp_server.arxiv_api import stream_pdf_to_path, ArxivTimeoutError
        from pathlib import Path
        import tempfile

        mock_paper = MagicMock()
        mock_paper.get_short_id.return_value = "2103.14030"

        # Mock a trickling stream that yields 1 byte at a time indefinitely
        class TricklingStream:
            def __iter__(self):
                return self

            def __next__(self):
                # Yield forever until deadline cuts us off
                return b"x"

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"

            with (
                patch("time.monotonic") as mock_monotonic,
                patch("time.sleep") as mock_sleep,
            ):
                call_count = [-1]  # Start at -1 so first call returns 0.0

                def mock_time():
                    call_count[0] += 1
                    # Progress 0.15s per call to simulate slow trickle
                    # With 2.0s deadline, will timeout after several iterations
                    return call_count[0] * 0.15

                mock_monotonic.side_effect = mock_time

                with patch("httpx.Client") as mock_client_cls:
                    mock_client = MagicMock()
                    mock_response = MagicMock()
                    mock_response.raise_for_status = MagicMock()
                    mock_response.iter_raw = MagicMock(return_value=TricklingStream())
                    mock_stream = MagicMock()
                    mock_stream.__enter__ = MagicMock(return_value=mock_response)
                    mock_stream.__exit__ = MagicMock(return_value=False)
                    mock_client.stream.return_value = mock_stream
                    mock_client.__enter__ = MagicMock(return_value=mock_client)
                    mock_client.__exit__ = MagicMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    # Provide explicit deadline 2.0s from start (0.0)
                    with pytest.raises(ArxivTimeoutError) as exc_info:
                        stream_pdf_to_path(
                            mock_paper,
                            pdf_path,
                            request_timeout=30.0,
                            user_agent="test",
                            deadline=2.0,
                        )

                    # Should be caught by deadline check in iter_raw loop
                    error_msg = str(exc_info.value).lower()
                    assert (
                        "exceeded deadline" in error_msg or "trickle" in error_msg
                    ), f"Expected deadline/trickle error, got: {exc_info.value}"

    @pytest.mark.asyncio
    async def test_html_plus_pdf_stall_single_budget(self):
        """HTML stall + PDF stall must complete within single deadline."""
        from arxiv_mcp_server.tools.download import (
            _fetch_html_content,
            _fetch_pdf_content,
        )
        from arxiv_mcp_server import arxiv_api

        # Mock HTML to return None (triggering PDF fallback)
        with patch(
            "arxiv_mcp_server.tools.download._fetch_html_content_single_attempt"
        ) as mock_html:
            # HTML times out after consuming some budget
            mock_html.side_effect = [
                httpx.TimeoutException("timeout"),
                httpx.TimeoutException("timeout"),
            ]

            with (
                patch("time.monotonic") as mock_monotonic,
                patch("time.sleep") as mock_sleep,
                patch.object(
                    arxiv_api.ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f: f()
                ),
            ):
                # Deadline at 2.0s, HTML uses 1.2s, leaving 0.8s for PDF
                mock_monotonic.side_effect = [
                    0.0,
                    0.0,
                    0.6,
                    0.6,
                    1.2,
                    1.2,
                ]  # After HTML retries

                deadline = 2.0
                result = _fetch_html_content("2103.14030", deadline)

                # HTML should have failed and returned None
                assert result is None
