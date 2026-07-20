"""Concurrency tests for the process-wide arXiv request limiter."""

import asyncio
import time

import pytest

from arxiv_mcp_server.arxiv_api import ArxivRateLimiter


@pytest.mark.asyncio
async def test_async_requests_are_serialized():
    limiter = ArxivRateLimiter(min_interval=0.0)
    active = 0
    max_active = 0

    async def request():
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    await asyncio.gather(limiter.run_async(request), limiter.run_async(request))

    assert max_active == 1


@pytest.mark.asyncio
async def test_async_request_starts_are_spaced_with_fake_clock():
    now = 0.0
    started = []

    async def advance_clock(delay: float) -> None:
        nonlocal now
        now += delay

    limiter = ArxivRateLimiter(
        min_interval=3.0,
        clock=lambda: now,
        async_sleep=advance_clock,
    )

    async def request():
        started.append(now)

    await limiter.run_async(request)
    await limiter.run_async(request)

    assert started == [0.0, 3.0]


@pytest.mark.asyncio
async def test_sync_and_async_requests_share_the_same_gate():
    limiter = ArxivRateLimiter(min_interval=0.0)
    active = 0
    max_active = 0

    def sync_request():
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.01)
        active -= 1

    async def async_request():
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    await asyncio.gather(
        asyncio.to_thread(limiter.run_sync, sync_request),
        limiter.run_async(async_request),
    )

    assert max_active == 1
