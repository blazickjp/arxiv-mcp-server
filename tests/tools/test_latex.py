"""Tests for safe, bounded arXiv LaTeX source tools."""

from __future__ import annotations

import io
import json
import tarfile
from unittest.mock import MagicMock

import pytest

from arxiv_mcp_server.tools import latex
