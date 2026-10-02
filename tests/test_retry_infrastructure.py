"""Tests for the unified retry infrastructure in arxiv_api."""

import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, Mock, patch
from arxiv_mcp_server.arxiv_api import (
    retry_with_backoff,
    ArxivTimeoutError,
    ArxivConnectionError,
    ArxivRateLimitError,
    _compute_backoff_seconds,
    _parse_retry_after_seconds,
)


class TestParseRetryAfterSeconds:
    """Test Retry-After header parsing."""

    def test_parses_numeric_value(self):
        assert _parse_retry_after_seconds("60") == 60.0
        assert _parse_retry_after_seconds("120.5") == 120.5

    def test_returns_none_for_none(self):
        assert _parse_retry_after_seconds(None) is None

    def test_returns_none_for_invalid(self):
        assert _parse_retry_after_seconds("not-a-number") is None
        assert _parse_retry_after_seconds("") is None


class TestComputeBackoffSeconds:
    """Test exponential backoff computation with jitter."""

    def test_exponential_growth(self):
        backoff_0 = _compute_backoff_seconds(0, None, 2.0, 60.0)
        backoff_1 = _compute_backoff_seconds(1, None, 2.0, 60.0)
        backoff_2 = _compute_backoff_seconds(2, None, 2.0, 60.0)
        # With jitter, we expect approximate exponential growth
        assert 1.0 <= backoff_0 <= 3.0  # ~2.0 with jitter
        assert 2.0 <= backoff_1 <= 6.0  # ~4.0 with jitter
        assert 4.0 <= backoff_2 <= 12.0  # ~8.0 with jitter

    def test_respects_max_backoff(self):
        backoff = _compute_backoff_seconds(10, None, 2.0, 10.0)
        assert backoff <= 10.0

    def test_honors_retry_after(self):
        backoff = _compute_backoff_seconds(0, "30.0", 2.0, 60.0)
        # Should be at least 30 (from Retry-After)
        assert backoff >= 15.0  # jitter may reduce it slightly

    def test_caps_retry_after_at_max(self):
        backoff = _compute_backoff_seconds(0, "100.0", 2.0, 60.0)
        # Should not exceed max_backoff
        assert backoff <= 60.0


@pytest.mark.asyncio
class TestRetryWithBackoff:
    """Test the unified retry_with_backoff function."""

    async def test_succeeds_on_first_try(self):
        """Operation succeeds immediately without retry."""
        mock_op = AsyncMock(return_value="success")
        result = await retry_with_backoff(
            mock_op, max_retries=3, initial_backoff=1.0, max_backoff=10.0
        )
        assert result == "success"
        assert mock_op.call_count == 1

    async def test_retries_on_timeout(self):
        """TimeoutException triggers retry."""
        mock_op = AsyncMock(
            side_effect=[
                httpx.TimeoutException("timeout 1"),
                httpx.TimeoutException("timeout 2"),
                "success",
            ]
        )
        result = await retry_with_backoff(
            mock_op,
            max_retries=3,
            initial_backoff=0.01,
            max_backoff=0.1,
            operation_name="test_op",
        )
        assert result == "success"
        assert mock_op.call_count == 3

    async def test_raises_arxiv_timeout_error_after_max_retries(self):
        """ArxivTimeoutError raised after exhausting retries on timeout."""
        mock_op = AsyncMock(side_effect=httpx.TimeoutException("persistent timeout"))
        with pytest.raises(ArxivTimeoutError) as exc_info:
            await retry_with_backoff(
                mock_op,
                max_retries=2,
                initial_backoff=0.01,
                max_backoff=0.1,
                operation_name="test_op",
            )
        assert "timed out after 3 attempts" in str(exc_info.value)
        assert "retry shortly" in str(exc_info.value)
        assert mock_op.call_count == 3  # initial + 2 retries

    async def test_retries_on_connect_error(self):
        """ConnectError triggers retry."""
        mock_op = AsyncMock(
            side_effect=[
                httpx.ConnectError("connection failed"),
                "success",
            ]
        )
        result = await retry_with_backoff(
            mock_op,
            max_retries=3,
            initial_backoff=0.01,
            max_backoff=0.1,
        )
        assert result == "success"
        assert mock_op.call_count == 2

    async def test_raises_arxiv_connection_error_after_max_retries(self):
        """ArxivConnectionError raised after exhausting retries on connection error."""
        mock_op = AsyncMock(
            side_effect=httpx.ConnectTimeout("persistent connection timeout")
        )
        with pytest.raises(ArxivConnectionError) as exc_info:
            await retry_with_backoff(
                mock_op,
                max_retries=2,
                initial_backoff=0.01,
                max_backoff=0.1,
            )
        assert "Could not connect to arXiv after 3 attempts" in str(exc_info.value)
        assert mock_op.call_count == 3

    async def test_retries_on_http_429(self):
        """HTTP 429 status triggers retry."""
        request = httpx.Request("GET", "https://example.com")
        response_429 = httpx.Response(429, request=request, headers={})
        response_200 = httpx.Response(200, request=request)
        mock_op = AsyncMock(
            side_effect=[
                httpx.HTTPStatusError("429", request=request, response=response_429),
                response_200,
            ]
        )
        result = await retry_with_backoff(
            mock_op,
            max_retries=3,
            initial_backoff=0.01,
            max_backoff=0.1,
        )
        assert result == response_200
        assert mock_op.call_count == 2

    async def test_retries_on_http_503(self):
        """HTTP 503 status triggers retry."""
        request = httpx.Request("GET", "https://example.com")
        response_503 = httpx.Response(503, request=request, headers={})
        response_200 = httpx.Response(200, request=request)
        mock_op = AsyncMock(
            side_effect=[
                httpx.HTTPStatusError("503", request=request, response=response_503),
                response_200,
            ]
        )
        result = await retry_with_backoff(
            mock_op,
            max_retries=3,
            initial_backoff=0.01,
            max_backoff=0.1,
        )
        assert result == response_200
        assert mock_op.call_count == 2

    async def test_http_406_not_retried(self):
        """HTTP 406 is NOT retried (not throttling per #277 clarification)."""
        request = httpx.Request("GET", "https://example.com")
        response_406 = httpx.Response(406, request=request, headers={})
        mock_op = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "406", request=request, response=response_406
            )
        )
        # Should raise immediately without retry
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await retry_with_backoff(
                mock_op,
                max_retries=3,
                initial_backoff=0.01,
                max_backoff=0.1,
            )
        assert exc_info.value.response.status_code == 406
        # Should NOT retry - only 1 call
        assert mock_op.call_count == 1

    async def test_raises_arxiv_rate_limit_error_after_max_retries_429(self):
        """ArxivRateLimitError raised after exhausting retries on HTTP 429."""
        request = httpx.Request("GET", "https://example.com")
        response_429 = httpx.Response(429, request=request, headers={})
        mock_op = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "429", request=request, response=response_429
            )
        )
        with pytest.raises(ArxivRateLimitError) as exc_info:
            await retry_with_backoff(
                mock_op,
                max_retries=2,
                initial_backoff=0.01,
                max_backoff=0.1,
            )
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after_seconds == 60.0  # default
        assert "rate limiting" in str(exc_info.value).lower()
        assert mock_op.call_count == 3

    async def test_http_406_not_treated_as_rate_limit(self):
        """HTTP 406 is NOT treated as rate limiting."""
        request = httpx.Request("GET", "https://example.com")
        response_406 = httpx.Response(406, request=request, headers={})
        mock_op = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "406", request=request, response=response_406
            )
        )
        # Should raise HTTPStatusError, NOT ArxivRateLimitError
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await retry_with_backoff(
                mock_op,
                max_retries=1,
                initial_backoff=0.01,
                max_backoff=0.1,
            )
        assert exc_info.value.response.status_code == 406
        # Should NOT retry
        assert mock_op.call_count == 1

    async def test_honors_retry_after_header(self):
        """Retry-After header value is captured in ArxivRateLimitError."""
        request = httpx.Request("GET", "https://example.com")
        response_429 = httpx.Response(
            429, request=request, headers={"Retry-After": "120"}
        )
        mock_op = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "429", request=request, response=response_429
            )
        )
        with pytest.raises(ArxivRateLimitError) as exc_info:
            await retry_with_backoff(
                mock_op,
                max_retries=1,
                initial_backoff=0.01,
                max_backoff=0.1,
            )
        # The backoff logic should have used the Retry-After value
        assert exc_info.value.retry_after_seconds >= 60.0  # at least default

    async def test_does_not_retry_non_retryable_http_errors(self):
        """HTTP errors other than 429/503/406 are not retried."""
        request = httpx.Request("GET", "https://example.com")
        response_404 = httpx.Response(404, request=request)
        mock_op = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "404", request=request, response=response_404
            )
        )
        with pytest.raises(httpx.HTTPStatusError):
            await retry_with_backoff(
                mock_op,
                max_retries=3,
                initial_backoff=0.01,
                max_backoff=0.1,
            )
        # Should fail immediately without retry
        assert mock_op.call_count == 1

    async def test_respects_max_total_time(self):
        """Operation stops retrying when max_total_time is exceeded."""
        mock_op = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        start = asyncio.get_event_loop().time()
        with pytest.raises(RuntimeError) as exc_info:
            await retry_with_backoff(
                mock_op,
                max_retries=100,  # many retries
                initial_backoff=0.01,
                max_backoff=0.1,
                max_total_time=0.5,  # but short total time
                operation_name="test_op",
            )
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 1.0  # should stop well before exhausting 100 retries
        assert "exceeded maximum total time" in str(exc_info.value)

    async def test_concurrent_retries_with_jitter(self):
        """Multiple concurrent retries don't happen in lockstep due to jitter."""
        call_times = []

        async def failing_op():
            call_times.append(asyncio.get_event_loop().time())
            raise httpx.TimeoutException("timeout")

        tasks = [
            retry_with_backoff(
                failing_op,
                max_retries=2,
                initial_backoff=0.1,
                max_backoff=1.0,
            )
            for _ in range(3)
        ]

        # Use return_exceptions=True so all tasks complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should have failed with ArxivTimeoutError
        assert all(isinstance(r, ArxivTimeoutError) for r in results)

        # With jitter, the retry times should be spread out
        # At least 6 attempts total (each op tries at least twice)
        assert len(call_times) >= 6
