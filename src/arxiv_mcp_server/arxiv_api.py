"""Shared compatibility helpers for the upstream arxiv package."""

import asyncio
import logging
import os
from pathlib import Path
import random
import tempfile
import threading
import time
from typing import Awaitable, Callable, Protocol, TypeVar

import httpx

logger = logging.getLogger("arxiv-mcp-server")
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


class RetryableError(Exception):
    """Base class for errors that should trigger a retry."""

    pass


class ArxivTimeoutError(RetryableError):
    """Raised when an arXiv request times out."""

    pass


class ArxivConnectionError(RetryableError):
    """Raised when a connection to arXiv fails."""

    pass


class ArxivRateLimitError(RetryableError):
    """Raised when arXiv returns rate-limiting status (429, 503, 406)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 429,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def _parse_retry_after_seconds(retry_after: str | None) -> float | None:
    """Parse Retry-After header into seconds.

    Supports both delay-seconds (integer) and HTTP-date formats.
    Returns None if unparseable or not provided.
    """
    if not retry_after:
        return None
    try:
        # Try as integer/float seconds first
        return float(retry_after)
    except ValueError:
        pass

    # Try as HTTP-date (RFC 7231)
    try:
        from email.utils import parsedate_to_datetime
        import datetime

        retry_dt = parsedate_to_datetime(retry_after)
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = (retry_dt - now).total_seconds()
        return max(0.0, delta)  # Don't return negative
    except Exception:
        logger.warning(f"Could not parse Retry-After header: {retry_after}")
        return None


def _compute_backoff_seconds(
    attempt: int,
    retry_after: str | None,
    initial_backoff: float,
    max_backoff: float,
) -> float:
    """Exponential backoff with jitter, with Retry-After as floor.

    Args:
        attempt: Zero-based retry attempt number.
        retry_after: Optional Retry-After header value (int or HTTP-date).
        initial_backoff: Initial backoff delay in seconds.
        max_backoff: Maximum backoff delay in seconds.

    Returns:
        Computed backoff delay in seconds with jitter applied.
        Retry-After (if provided) is used as a floor BEFORE jitter.
    """
    delay = min(initial_backoff * (2**attempt), max_backoff)

    # Parse and apply Retry-After as floor
    parsed_retry_after = _parse_retry_after_seconds(retry_after)
    if parsed_retry_after is not None:
        delay = max(delay, parsed_retry_after)

    # Apply jitter (0.5 to 1.0 multiplier) but respect Retry-After floor
    jittered = delay * (0.5 + random.random())
    if parsed_retry_after is not None:
        jittered = max(jittered, parsed_retry_after)

    return min(jittered, max_backoff)


async def retry_with_backoff(
    operation: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    initial_backoff: float = 2.0,
    max_backoff: float = 60.0,
    max_total_time: float | None = None,
    operation_name: str = "operation",
) -> T:
    """Execute an async operation with exponential backoff on retryable errors.

    Retries on:
    - httpx.TimeoutException
    - httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError
    - httpx.HTTPStatusError with status 429, 503, 406

    Note: HTTP 406 uses minimal retries (ARXIV_HTTP_406_MAX_RETRIES = 1)
    because arXiv's 406 is IP-level burst throttling and retrying inside
    the window extends the block (#277).

    Args:
        operation: Async callable to execute.
        max_retries: Maximum number of retry attempts (for 429/503).
        initial_backoff: Initial backoff delay in seconds.
        max_backoff: Maximum backoff delay in seconds.
        max_total_time: Optional maximum total time in seconds (raises on exceed).
        operation_name: Name for logging.

    Returns:
        Result of the operation.

    Raises:
        Original exception after exhausting retries, wrapped with context.
    """
    from .config import Settings

    settings = Settings()
    start_time = time.monotonic()
    last_exception: Exception | None = None
    retry_after: str | None = None

    for attempt in range(max_retries + 1):
        if max_total_time is not None:
            elapsed = time.monotonic() - start_time
            if elapsed >= max_total_time:
                logger.error(
                    "%s exceeded max total time %.1fs after %d attempts",
                    operation_name,
                    max_total_time,
                    attempt,
                )
                if last_exception:
                    raise ArxivTimeoutError(
                        f"{operation_name} timed out after {max_total_time:.0f}s. "
                        f"The arXiv API may be slow or overloaded. Please retry shortly."
                    ) from last_exception
                raise ArxivTimeoutError(
                    f"{operation_name} timed out after {max_total_time:.0f}s. "
                    f"The arXiv API may be slow or overloaded. Please retry shortly."
                )

        try:
            # Always enforce remaining budget with asyncio.wait_for
            if max_total_time is not None:
                remaining = max_total_time - (time.monotonic() - start_time)
                if remaining <= 0:
                    raise asyncio.TimeoutError("Budget exhausted before attempt")
                logger.debug(
                    "%s attempt %d with %.1fs budget",
                    operation_name,
                    attempt + 1,
                    remaining,
                )
                return await asyncio.wait_for(operation(), timeout=remaining)

            return await operation()
        except asyncio.TimeoutError as e:
            # Raised by wait_for when budget expires during an operation
            if max_total_time is not None:
                elapsed = time.monotonic() - start_time
                logger.error(
                    "%s timed out after %.1fs (budget: %.1fs)",
                    operation_name,
                    elapsed,
                    max_total_time,
                )
                # Determine if this was due to rate limiting
                if last_exception and isinstance(last_exception, httpx.HTTPStatusError):
                    status_code = last_exception.response.status_code
                    if status_code in (429, 503, 406):
                        # Budget expired while handling rate limit
                        retry_after = 60.0 if status_code != 406 else 600.0
                        raise ArxivRateLimitError(
                            f"arXiv is rate limiting this IP (HTTP {status_code}). "
                            f"Please wait {int(retry_after)} seconds before retrying.",
                            status_code=status_code,
                            retry_after_seconds=retry_after,
                        ) from e
                # Otherwise it's a genuine timeout
                raise ArxivTimeoutError(
                    f"arXiv request timed out after {int(elapsed)}s. "
                    f"The arXiv API may be slow or overloaded. Please retry shortly."
                ) from e
            # Re-raise if no budget was set
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as e:
            last_exception = e
            if attempt < max_retries:
                wait = _compute_backoff_seconds(
                    attempt, None, initial_backoff, max_backoff
                )
                # Cap wait to remaining budget if set
                if max_total_time is not None:
                    remaining = max_total_time - (time.monotonic() - start_time)
                    if wait >= remaining:
                        # Not enough budget for backoff + retry
                        logger.warning(
                            "%s insufficient budget for retry (%.1fs < %.1fs wait)",
                            operation_name,
                            remaining,
                            wait,
                        )
                        raise ArxivConnectionError(
                            f"Could not connect to arXiv after {attempt + 1} attempts. "
                            f"Please check your network connection and retry shortly."
                        ) from e
                    wait = min(wait, remaining * 0.9)  # Leave 10% buffer
                logger.warning(
                    "%s connection error; retrying in %.1fs (attempt %d/%d)",
                    operation_name,
                    wait,
                    attempt + 1,
                    max_retries + 1,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "%s connection failed after %d attempts",
                    operation_name,
                    max_retries + 1,
                )
                raise ArxivConnectionError(
                    f"Could not connect to arXiv after {max_retries + 1} attempts. "
                    f"Please check your network connection and retry shortly."
                ) from e
        except httpx.TimeoutException as e:
            last_exception = e
            if attempt < max_retries:
                wait = _compute_backoff_seconds(
                    attempt, None, initial_backoff, max_backoff
                )
                # Cap wait to remaining budget if set
                if max_total_time is not None:
                    remaining = max_total_time - (time.monotonic() - start_time)
                    if wait >= remaining or remaining <= 0.1:
                        logger.warning(
                            "%s insufficient budget for retry (%.1fs remaining, %.1fs wait needed)",
                            operation_name,
                            remaining,
                            wait,
                        )
                        raise ArxivTimeoutError(
                            f"arXiv request timed out after {attempt + 1} attempts. "
                            f"The arXiv API may be slow or overloaded. Please retry shortly."
                        ) from e
                    wait = min(wait, remaining * 0.9)  # Leave 10% buffer
                logger.warning(
                    "%s timed out; retrying in %.1fs (attempt %d/%d)",
                    operation_name,
                    wait,
                    attempt + 1,
                    max_retries + 1,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "%s timed out after %d attempts", operation_name, max_retries + 1
                )
                raise ArxivTimeoutError(
                    f"arXiv request timed out after {max_retries + 1} attempts. "
                    f"The arXiv API may be slow or overloaded. Please retry shortly."
                ) from e
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code in (429, 503, 406):
                # 406 is IP-level burst throttling, retry minimally (#277)
                last_exception = e
                # Use minimal retry count for 406 to avoid prolonging the block
                max_retries_for_status = (
                    settings.ARXIV_HTTP_406_MAX_RETRIES
                    if e.response.status_code == 406
                    else max_retries
                )
                retry_after = e.response.headers.get("Retry-After")
                if attempt < max_retries_for_status:
                    wait = _compute_backoff_seconds(
                        attempt, retry_after, initial_backoff, max_backoff
                    )
                    # Cap wait to remaining budget if set
                    if max_total_time is not None:
                        remaining = max_total_time - (time.monotonic() - start_time)
                        if wait >= remaining:
                            # Retry-After or backoff exceeds budget - return rate_limited immediately
                            parsed_retry_after = _parse_retry_after_seconds(retry_after)
                            if parsed_retry_after is None:
                                parsed_retry_after = (
                                    600.0 if e.response.status_code == 406 else 60.0
                                )
                            logger.warning(
                                "%s HTTP %d: Retry-After (%.1fs) exceeds budget (%.1fs), returning immediately",
                                operation_name,
                                e.response.status_code,
                                wait,
                                remaining,
                            )
                            raise ArxivRateLimitError(
                                f"arXiv is rate limiting this IP (HTTP {e.response.status_code}). "
                                f"Please wait {int(parsed_retry_after)} seconds before retrying.",
                                status_code=e.response.status_code,
                                retry_after_seconds=parsed_retry_after,
                            ) from e
                        wait = min(wait, remaining * 0.9)  # Leave 10% buffer
                    logger.warning(
                        "%s HTTP %d; retrying in %.1fs (attempt %d/%d)",
                        operation_name,
                        e.response.status_code,
                        wait,
                        attempt + 1,
                        max_retries_for_status + 1,
                    )
                    await asyncio.sleep(wait)
                else:
                    parsed_retry_after = _parse_retry_after_seconds(retry_after)
                    if parsed_retry_after is None:
                        # 406 gets 10-minute hint, others get 1 minute
                        parsed_retry_after = (
                            600.0 if e.response.status_code == 406 else 60.0
                        )
                    logger.error(
                        "%s HTTP %d after %d attempts",
                        operation_name,
                        e.response.status_code,
                        max_retries_for_status + 1,
                    )
                    raise ArxivRateLimitError(
                        f"arXiv is rate limiting this IP (HTTP {e.response.status_code}). "
                        f"Please wait {int(parsed_retry_after)} seconds before retrying.",
                        status_code=e.response.status_code,
                        retry_after_seconds=parsed_retry_after,
                    ) from e
            else:
                # Non-retryable HTTP errors
                raise
        except (ArxivTimeoutError, ArxivConnectionError, ArxivRateLimitError):
            raise
        except Exception:
            raise

    if last_exception:
        raise last_exception
    raise RuntimeError(f"{operation_name} failed after exhausting retries")


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
    deadline: float | None = None,
) -> None:
    """Stream an arXiv PDF to disk with bounded memory usage.

    Handles arXiv HTTP 406/429/503 (throttling) with retries and exponential backoff.
    HTTP 406 uses minimal retries to avoid prolonging IP-level block (#277).

    Args:
        paper: arXiv result with PDF URL.
        destination: Path where the PDF will be saved.
        request_timeout: Per-attempt read timeout in seconds.
        user_agent: User-Agent string for HTTP requests.
        deadline: Optional wall-clock deadline (time.monotonic()) for the entire
            download_paper operation. When provided, this function caps its attempts
            to the remaining time and starts a fresh budget only if deadline is None.
    """
    from .config import Settings

    settings = Settings()
    headers = {"User-Agent": user_agent}
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".part",
    )
    os.close(descriptor)
    staging = Path(staging_name)

    # Use provided deadline or start fresh budget
    start_time = time.monotonic()
    if deadline is None:
        max_total_time = float(settings.ARXIV_MAX_TOTAL_TIME)
        deadline = start_time + max_total_time

    def remaining_time() -> float:
        return max(0.0, deadline - time.monotonic())

        def sync_download(attempt_timeout: float) -> None:
            """Synchronous download operation with capped timeout."""
            timeout = httpx.Timeout(
                connect=float(settings.ARXIV_CONNECT_TIMEOUT),
                read=attempt_timeout,
                write=30.0,
                pool=30.0,
            )
            with httpx.Client(
                timeout=timeout, follow_redirects=True, headers=headers
            ) as client:
                with client.stream("GET", canonical_pdf_url(paper)) as response:
                    response.raise_for_status()
                    with staging.open("wb") as output:
                        # Use iter_raw() instead of iter_bytes() for true per-read deadline check
                        # iter_bytes() buffers until chunk_size arrives, so with 1-byte drip
                        # the deadline check never runs
                        for chunk in response.iter_raw(chunk_size=32 * 1024):
                            # Check wall-clock deadline for trickling responses
                            if time.monotonic() >= deadline:
                                raise ArxivTimeoutError(
                                    "PDF download exceeded deadline (trickling response). "
                                    "Please retry shortly."
                                )
                            if chunk:  # iter_raw can return empty chunks
                                output.write(chunk)

    try:
        last_exception: Exception | None = None

        for attempt in range(settings.ARXIV_MAX_RETRIES + 1):
            # Check remaining budget before attempt
            remaining = remaining_time()
            if remaining <= 0.1:
                staging.unlink(missing_ok=True)
                raise ArxivTimeoutError(
                    f"PDF download budget exhausted. Please retry shortly."
                )

            try:
                # Cap this attempt's timeout to remaining budget
                attempt_timeout = min(request_timeout, remaining * 0.9)
                if attempt_timeout < 1.0:
                    # Not enough time for a meaningful attempt
                    staging.unlink(missing_ok=True)
                    raise ArxivTimeoutError(
                        f"PDF download timed out (insufficient budget for retry). "
                        f"Please retry shortly."
                    )
                sync_download(attempt_timeout)
                staging.replace(destination)
                return
            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < settings.ARXIV_MAX_RETRIES:
                    wait = min(
                        settings.ARXIV_INITIAL_BACKOFF
                        * (2**attempt)
                        * (0.5 + random.random()),
                        settings.ARXIV_MAX_BACKOFF,
                    )
                    # Cap wait to remaining budget
                    remaining = remaining_time()
                    if wait >= remaining:
                        staging.unlink(missing_ok=True)
                        raise ArxivTimeoutError(
                            f"PDF download timed out (insufficient budget for retry). "
                            f"Please retry shortly."
                        ) from e
                    wait = min(wait, remaining * 0.9)

                    logger.warning(
                        "PDF download timed out; retrying in %.1fs (attempt %d/%d)",
                        wait,
                        attempt + 1,
                        settings.ARXIV_MAX_RETRIES + 1,
                    )
                    time.sleep(wait)
                else:
                    staging.unlink(missing_ok=True)
                    raise ArxivTimeoutError(
                        f"arXiv PDF download timed out after {settings.ARXIV_MAX_RETRIES + 1} attempts. "
                        f"The arXiv PDF server may be slow or overloaded. Please retry shortly."
                    ) from e
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_exception = e
                if attempt < settings.ARXIV_MAX_RETRIES:
                    wait = min(
                        settings.ARXIV_INITIAL_BACKOFF
                        * (2**attempt)
                        * (0.5 + random.random()),
                        settings.ARXIV_MAX_BACKOFF,
                    )
                    # Cap wait to remaining budget
                    remaining = remaining_time()
                    if wait >= remaining:
                        staging.unlink(missing_ok=True)
                        raise ArxivTimeoutError(
                            f"Could not connect to arXiv for PDF download (insufficient budget for retry)"
                        ) from e
                    wait = min(wait, remaining * 0.9)

                    logger.warning(
                        "PDF download connection error; retrying in %.1fs (attempt %d/%d)",
                        wait,
                        attempt + 1,
                        settings.ARXIV_MAX_RETRIES + 1,
                    )
                    time.sleep(wait)
                else:
                    staging.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Could not connect to arXiv for PDF download after {settings.ARXIV_MAX_RETRIES + 1} attempts"
                    ) from e
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code in (406, 429, 503):
                    # 406 is IP-level burst throttling, retry minimally (#277)
                    last_exception = e
                    max_retries_for_status = (
                        settings.ARXIV_HTTP_406_MAX_RETRIES
                        if e.response.status_code == 406
                        else settings.ARXIV_MAX_RETRIES
                    )
                    if attempt < max_retries_for_status:
                        retry_after = e.response.headers.get("Retry-After")
                        parsed_retry_after = _parse_retry_after_seconds(retry_after)

                        # If Retry-After exceeds max_backoff or remaining budget,
                        # return rate_limited immediately instead of sleeping and retrying
                        remaining = remaining_time()
                        if parsed_retry_after is not None:
                            if (
                                parsed_retry_after > settings.ARXIV_MAX_BACKOFF
                                or parsed_retry_after >= remaining
                            ):
                                staging.unlink(missing_ok=True)
                                raise ArxivRateLimitError(
                                    f"arXiv is rate limiting this IP (HTTP {e.response.status_code}). "
                                    f"Please wait {int(parsed_retry_after)} seconds before retrying.",
                                    status_code=e.response.status_code,
                                    retry_after_seconds=parsed_retry_after,
                                ) from e

                        # Calculate exponential backoff
                        wait = min(
                            settings.ARXIV_INITIAL_BACKOFF
                            * (2**attempt)
                            * (0.5 + random.random()),
                            settings.ARXIV_MAX_BACKOFF,
                        )
                        # Use Retry-After as floor if available and within limits
                        if parsed_retry_after is not None:
                            wait = max(wait, parsed_retry_after)

                        # Cap wait to remaining budget
                        if wait >= remaining:
                            staging.unlink(missing_ok=True)
                            retry_after_seconds = (
                                parsed_retry_after
                                if parsed_retry_after is not None
                                else (600.0 if e.response.status_code == 406 else 60.0)
                            )
                            raise ArxivRateLimitError(
                                f"arXiv is rate limiting this IP (HTTP {e.response.status_code}). "
                                f"Please wait {int(retry_after_seconds)} seconds before retrying.",
                                status_code=e.response.status_code,
                                retry_after_seconds=retry_after_seconds,
                            ) from e
                        wait = min(wait, remaining * 0.9)

                        logger.warning(
                            "PDF download HTTP %d; retrying in %.1fs (attempt %d/%d)",
                            e.response.status_code,
                            wait,
                            attempt + 1,
                            max_retries_for_status + 1,
                        )
                        time.sleep(wait)
                    else:
                        staging.unlink(missing_ok=True)
                        status_code = e.response.status_code
                        retry_after_seconds = _parse_retry_after_seconds(
                            e.response.headers.get("Retry-After")
                        )
                        if retry_after_seconds is None:
                            retry_after_seconds = 600.0 if status_code == 406 else 60.0
                        message = (
                            f"arXiv is rate limiting this IP (HTTP {status_code}). "
                            f"Please wait {int(retry_after_seconds)} seconds before retrying."
                        )
                        raise ArxivRateLimitError(
                            message,
                            status_code=status_code,
                            retry_after_seconds=retry_after_seconds,
                        ) from e
                else:
                    # Non-retryable HTTP error
                    staging.unlink(missing_ok=True)
                    status = (
                        e.response.status_code if e.response is not None else "unknown"
                    )
                    raise RuntimeError(
                        f"arXiv PDF download HTTP error (HTTP {status})"
                    ) from e

    except BaseException:
        staging.unlink(missing_ok=True)
        raise
