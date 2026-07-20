"""Shared compatibility helpers for the upstream arxiv package."""

import asyncio
from pathlib import Path
import threading
import time
from typing import Awaitable, Callable, Protocol, TypeVar

import httpx

T = TypeVar("T")


class ArxivRateLimiter:
    """Serialize and space arXiv API requests across sync and async callers."""

    def __init__(
        self,
        min_interval: float = 3.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sync_sleep: Callable[[float], None] = time.sleep,
        async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sync_sleep = sync_sleep
        self._async_sleep = async_sleep
        self._lock = threading.Lock()
        self._last_started: float | None = None

    def _remaining_delay(self) -> float:
        if self._last_started is None:
            return 0.0
        return max(0.0, self.min_interval - (self._clock() - self._last_started))

    def run_sync(self, operation: Callable[[], T]) -> T:
        """Run a blocking operation inside the shared request gate."""
        with self._lock:
            delay = self._remaining_delay()
            if delay:
                self._sync_sleep(delay)
            self._last_started = self._clock()
            return operation()

    async def run_async(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an async operation inside the same gate used by sync callers."""
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            delay = self._remaining_delay()
            if delay:
                await self._async_sleep(delay)
            self._last_started = self._clock()
            return await operation()
        finally:
            self._lock.release()


ARXIV_RATE_LIMITER = ArxivRateLimiter()


class ArxivResult(Protocol):
    """Subset of arxiv.Result used by compatibility helpers."""

    def get_short_id(self) -> str: ...


def canonical_pdf_url(paper: ArxivResult) -> str:
    """Return the stable public PDF URL for an arXiv result.

    arxiv 4 removed ``Result.pdf_url`` and ``Result.download_pdf`` but retained
    ``get_short_id``. Building the canonical URL from that stable identifier
    keeps the server compatible across arxiv 2.x through 4.x.
    """
    return f"https://arxiv.org/pdf/{paper.get_short_id()}.pdf"


def stream_pdf_to_path(
    paper: ArxivResult,
    destination: Path,
    *,
    request_timeout: float,
    user_agent: str,
) -> None:
    """Stream an arXiv PDF to disk with bounded memory usage."""
    timeout = httpx.Timeout(
        connect=30.0,
        read=max(120.0, request_timeout),
        write=30.0,
        pool=30.0,
    )
    headers = {"User-Agent": user_agent}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_suffix(f"{destination.suffix}.part")
    staging.unlink(missing_ok=True)

    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers=headers
        ) as client:
            with client.stream("GET", canonical_pdf_url(paper)) as response:
                response.raise_for_status()
                with staging.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=256 * 1024):
                        output.write(chunk)
        staging.replace(destination)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
