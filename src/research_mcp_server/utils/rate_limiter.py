"""Token bucket rate limiter for external API calls."""

import asyncio
import time


class RateLimiter:
    """Simple async rate limiter using minimum interval between calls.

    Args:
        calls_per_second: Maximum number of calls allowed per second.
            Default 0.33 = 1 request per 3 seconds (arXiv ToU).
    """

    def __init__(self, calls_per_second: float = 0.33) -> None:
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Wait until we're allowed to make the next call."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self.last_call = time.monotonic()


# Pre-configured limiters
arxiv_limiter = RateLimiter(calls_per_second=0.33)  # arXiv: max 1 req per 3s
s2_limiter = RateLimiter(calls_per_second=10)  # S2 unauthenticated: be polite
openalex_limiter = RateLimiter(calls_per_second=10)  # OpenAlex: 10 req/s without mailto
crossref_limiter = RateLimiter(calls_per_second=10)  # Crossref: polite pool with mailto
hf_limiter = RateLimiter(calls_per_second=10)  # Hugging Face: ~10 req/s polite
hn_limiter = RateLimiter(calls_per_second=10)  # Hacker News: ~10 req/s polite
devto_limiter = RateLimiter(calls_per_second=10)  # Dev.to: ~10 req/s polite
lobsters_limiter = RateLimiter(calls_per_second=0.5)  # Lobsters: be polite, ~1 req/2s
npm_limiter = RateLimiter(calls_per_second=10)  # npm: generous limits
pypi_limiter = RateLimiter(calls_per_second=10)  # PyPI: generous limits
crates_limiter = RateLimiter(calls_per_second=10)  # crates.io: generous limits
