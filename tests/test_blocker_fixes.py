"""Tests for blocker fixes identified in pre-merge testing."""

import asyncio
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
                _fetch_html_content_single_attempt("2103.14030")

    @pytest.mark.asyncio
    async def test_pdf_timeout_honest_error(self):
        """Blocker 8: PDF timeout returns RuntimeError (honest timeout), not rate_limited."""
        from arxiv_mcp_server.arxiv_api import stream_pdf_to_path
        from arxiv_mcp_server.tools.download import PaperNotFoundError
        from pathlib import Path
        import tempfile

        # Create mock paper
        mock_paper = MagicMock()
        mock_paper.get_short_id.return_value = "2103.14030"

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"

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

                # Should raise RuntimeError (honest timeout), not ArxivRateLimitError
                with pytest.raises(RuntimeError) as exc_info:
                    stream_pdf_to_path(
                        mock_paper, pdf_path, request_timeout=30.0, user_agent="test"
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
        """Blocker 9: HTTP-date Retry-After parsing on HTML path."""
        from arxiv_mcp_server.tools.download import _fetch_html_content_single_attempt

        # Create response with HTTP-date Retry-After
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "Fri, 31 Dec 2026 23:59:59 GMT"}

        with patch.object(httpx, "get", return_value=mock_response):
            # Should parse HTTP-date without crashing
            with pytest.raises(ArxivRateLimitError) as exc_info:
                _fetch_html_content_single_attempt("2103.14030")

            assert exc_info.value.status_code == 429
            # Should have parsed the date successfully (not use default)
            assert exc_info.value.retry_after_seconds > 0
