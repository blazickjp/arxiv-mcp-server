"""Safe, bounded retrieval of original LaTeX sources from arXiv."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import gzip
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import tarfile
import tempfile
import threading
from typing import Any

import httpx
import mcp.types as types
from mcp.types import ToolAnnotations

from ..arxiv_api import ARXIV_RATE_LIMITER
from ..config import Settings
from .content import add_content_payload
from .list_papers import is_valid_arxiv_id

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2_000
MAX_ARCHIVE_PATH_BYTES = 512
MAX_ARCHIVE_PATH_DEPTH = 20
MAX_MEMBER_BYTES = 10 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_TEX_FILES = 500
MAX_TOTAL_TEX_BYTES = 50 * 1024 * 1024
MAX_FLATTENED_CHARS = 50 * 1024 * 1024
MAX_INCLUDE_DEPTH = 20
MAX_SECTION_COUNT = 10_000
MAX_SECTION_TITLE_CHARS = 200
DEFAULT_MAX_SECTIONS = 100
MAX_RETURNED_SECTIONS = 200
CACHE_FORMAT_VERSION = 2
MAX_PAPER_ID_CHARS = 40
MAX_SECTION_ID_CHARS = 200
DEFAULT_MAX_CHARS = 12_000
MAX_RETURN_CHARS = 50_000
MAX_MACRO_ROUNDS = 8
MAX_REPORTED_UNMATCHED = 8
