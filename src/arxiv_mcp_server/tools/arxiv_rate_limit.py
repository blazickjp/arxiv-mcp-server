"""Shared arXiv request throttling helpers."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Callable, TypeVar

import httpx

from ..config import Settings

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

MIN_REQUEST_INTERVAL = 3.0
RATE_LIMIT_WAIT_SECONDS = 600

ARXIV_HEADERS = {
    "User-Agent": (
        f"{settings.APP_NAME}/{settings.APP_VERSION} "
        "(https://github.com/blazickjp/arxiv-mcp-server; research tool)"
    )
}

_last_request_time: float = 0.0
_cooldown_until: float = 0.0
_request_lock = asyncio.Lock()
T = TypeVar("T")


class ArxivCooldownError(RuntimeError):
    """Raised when local arXiv cooldown is active."""


def _cooldown_remaining(now: float | None = None) -> int:
    current = time.monotonic() if now is None else now
    return max(0, int(_cooldown_until - current))


def _start_cooldown(reason: str) -> None:
    """Prevent further arXiv requests for the configured cooldown period."""
    global _cooldown_until

    _cooldown_until = max(_cooldown_until, time.monotonic() + RATE_LIMIT_WAIT_SECONDS)
    logger.warning(
        "Entering arXiv cooldown for %s seconds: %s",
        RATE_LIMIT_WAIT_SECONDS,
        reason,
    )


def _raise_if_cooling_down() -> None:
    remaining = _cooldown_remaining()
    if remaining > 0:
        raise ArxivCooldownError(
            f"arXiv is cooling down after a rate-limit response. "
            f"Please wait about {remaining} seconds before retrying."
        )


def _looks_like_rate_limit(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "429",
            "503",
            "rate limit",
            "rate-limit",
            "too many requests",
            "timed out",
            "timeout",
        )
    )


@asynccontextmanager
async def arxiv_request_slot():
    """Hold the shared arXiv request lock for one complete upstream request."""
    global _last_request_time

    async with _request_lock:
        _raise_if_cooling_down()
        elapsed = time.monotonic() - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _raise_if_cooling_down()
        _last_request_time = time.monotonic()
        yield


async def wait_for_arxiv_slot() -> None:
    """Serialize outbound arXiv request starts and enforce a minimum spacing."""
    async with arxiv_request_slot():
        return


async def run_sync_with_arxiv_slot(func: Callable[[], T]) -> T:
    """Run a blocking arXiv operation while holding the shared request lock."""
    async with arxiv_request_slot():
        try:
            return await asyncio.to_thread(func)
        except Exception as exc:
            if _looks_like_rate_limit(exc):
                _start_cooldown(str(exc))
            raise


async def rate_limited_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Make a GET request while respecting arXiv's rate limit policy."""
    async with arxiv_request_slot():
        for attempt in range(2):
            try:
                response = await client.get(url, headers=ARXIV_HEADERS)
                if response.status_code in (429, 503):
                    logger.warning(
                        "arXiv rate limited this IP: HTTP %s", response.status_code
                    )
                    _start_cooldown(f"HTTP {response.status_code}")
                    raise RuntimeError(
                        f"arXiv is rate limiting this IP (HTTP {response.status_code}). "
                        f"Please wait {RATE_LIMIT_WAIT_SECONDS} seconds before retrying."
                    )
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                if attempt == 0:
                    logger.warning("arXiv request timed out, retrying once")
                    await asyncio.sleep(5.0)
                    continue
                _start_cooldown("request timeout")
                raise

    raise RuntimeError("arXiv request timed out after retry")
