"""Search functionality for the arXiv MCP server."""

import arxiv
import json
import logging
import httpx
import asyncio
import xml.etree.ElementTree as ET
from urllib.parse import quote
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from dateutil import parser
import mcp.types as types
from mcp.types import ToolAnnotations
from ..config import Settings, get_arxiv_client
from ..arxiv_api import ARXIV_RATE_LIMITER, canonical_pdf_url

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

ARXIV_HEADERS = {
    "User-Agent": (
        f"{settings.APP_NAME}/{settings.APP_VERSION} "
        "(https://github.com/blazickjp/arxiv-mcp-server; research tool)"
    )
}


async def _rate_limited_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Make an HTTP request through the process-wide arXiv request gate."""

    async def request() -> httpx.Response:
        for attempt in range(2):
            try:
                response = await client.get(url, headers=ARXIV_HEADERS)
                if response.status_code in (429, 503):
                    logger.warning(
                        "arXiv rate limited (%s); not retrying", response.status_code
                    )
                    raise RuntimeError(
                        f"arXiv is rate limiting this IP (HTTP {response.status_code}). "
                        "Please wait 60 seconds before retrying."
                    )
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                if attempt == 0:
                    logger.warning("arXiv request timed out, retrying once")
                    await asyncio.sleep(5.0)
                else:
                    raise
        raise RuntimeError("arXiv request timed out after retry")

    return await ARXIV_RATE_LIMITER.run_async(request)
