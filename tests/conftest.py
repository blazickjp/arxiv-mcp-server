"""Shared test fixtures for the arXiv MCP server test suite."""

import pytest
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock
import arxiv
from pathlib import Path
from arxiv_mcp_server import config as _config


class MockAuthor:
    def __init__(self, name):
        self.name = name


class MockLink:
    def __init__(self, href):
        self.href = href


@pytest.fixture(autouse=True)
def reset_arxiv_client():
    """Reset the shared arxiv client singleton between tests."""
    _config._arxiv_client = None
    yield
    _config._arxiv_client = None


@pytest.fixture
def mock_paper():
    """Create a properly structured mock paper with all required attributes."""
    paper = MagicMock(spec=arxiv.Result)
    paper.get_short_id.return_value = "2103.12345"
    paper.title = "Test Paper"
    paper.authors = [MockAuthor("John Doe"), MockAuthor("Jane Smith")]
    paper.summary = "Test abstract"
    paper.categories = ["cs.AI", "cs.LG"]
    paper.published = datetime(2023, 1, 1, tzinfo=timezone.utc)
    paper.pdf_url = "https://arxiv.org/pdf/2103.12345"
    paper.comment = "Test comment"
    paper.journal_ref = "Test Journal 2023"
    paper.primary_category = "cs.AI"
    paper.links = [MockLink("https://arxiv.org/abs/2103.12345")]
    return paper


@pytest.fixture
def mock_client(mock_paper):
    """Create a mock arxiv client with predefined behavior."""
    client = MagicMock(spec=arxiv.Client)
    client.results.return_value = [mock_paper]
    return client


@pytest.fixture
def temp_storage_path():
    """Create a temporary directory for paper storage during tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_pdf_content():
    """Create mock PDF content for testing."""
    return b"Mock PDF Content"


@pytest.fixture
def mock_http_response():
    """Create a mock HTTP response for testing paper downloads."""
    response = AsyncMock()
    response.status = 200
    response.__aenter__.return_value = response
    response.read.return_value = b"Mock PDF Content"
    return response


@pytest.fixture
def mock_http_session(mock_http_response):
    """Create a mock HTTP session for testing."""
    session = AsyncMock()
    session.get.return_value = mock_http_response
    session.__aenter__.return_value = session
    return session
