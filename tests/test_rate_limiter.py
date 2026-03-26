"""Tests for the async rate limiter utility."""

import asyncio
from unittest.mock import patch, AsyncMock

import pytest

from research_mcp_server.utils.rate_limiter import RateLimiter, arxiv_limiter, s2_limiter


@pytest.mark.asyncio
async def test_rate_limiter_first_call_immediate():
    """First call should not wait (no prior call recorded)."""
    limiter = RateLimiter(calls_per_second=1.0)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await limiter.wait()
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limiter_enforces_interval():
    """Rapid consecutive calls should trigger a sleep for the remaining interval."""
    limiter = RateLimiter(calls_per_second=1.0)  # min_interval = 1.0s

    # Simulate: first call at t=100, second call at t=100.3 (only 0.3s elapsed)
    monotonic_values = [100.0, 100.0, 100.3, 100.3]
    call_index = 0

    def fake_monotonic():
        nonlocal call_index
        val = monotonic_values[call_index]
        call_index += 1
        return val

    with (
        patch("time.monotonic", side_effect=fake_monotonic),
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        # First call: elapsed = 100.0 - 0.0 = 100.0 > 1.0, no sleep
        await limiter.wait()
        mock_sleep.assert_not_called()

        # Second call: elapsed = 100.3 - 100.0 = 0.3 < 1.0, should sleep 0.7s
        await limiter.wait()
        mock_sleep.assert_awaited_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert abs(sleep_duration - 0.7) < 0.01


@pytest.mark.asyncio
async def test_rate_limiter_allows_after_interval():
    """Call after sufficient time has passed should not wait."""
    limiter = RateLimiter(calls_per_second=1.0)  # min_interval = 1.0s

    # First call at t=100, second call at t=102 (2s elapsed, well past interval)
    monotonic_values = [100.0, 100.0, 102.0, 102.0]
    call_index = 0

    def fake_monotonic():
        nonlocal call_index
        val = monotonic_values[call_index]
        call_index += 1
        return val

    with (
        patch("time.monotonic", side_effect=fake_monotonic),
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        await limiter.wait()
        await limiter.wait()
        mock_sleep.assert_not_called()


def test_preconfigured_limiters_exist():
    """Verify arxiv_limiter and s2_limiter have correct intervals."""
    # arXiv: 0.33 calls/s -> ~3.03s interval
    assert isinstance(arxiv_limiter, RateLimiter)
    assert abs(arxiv_limiter.min_interval - (1.0 / 0.33)) < 0.01

    # S2: 10 calls/s -> 0.1s interval
    assert isinstance(s2_limiter, RateLimiter)
    assert abs(s2_limiter.min_interval - 0.1) < 0.01
