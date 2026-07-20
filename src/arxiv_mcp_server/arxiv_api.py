"""Shared compatibility helpers for the upstream arxiv package."""

from pathlib import Path
from typing import Protocol

import httpx


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

    with httpx.Client(
        timeout=timeout, follow_redirects=True, headers=headers
    ) as client:
        with client.stream("GET", canonical_pdf_url(paper)) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                for chunk in response.iter_bytes(chunk_size=256 * 1024):
                    output.write(chunk)
