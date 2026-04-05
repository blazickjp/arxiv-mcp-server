"""Tests for paper download functionality."""

import pytest
import json
from datetime import datetime
from arxiv_mcp_server.tools.download import (
    handle_download,
    get_paper_path,
    conversion_statuses,
)


@pytest.mark.asyncio
async def test_download_paper_lifecycle(mocker, temp_storage_path):
    """Test the complete lifecycle of downloading and converting a paper."""
    paper_id = "2103.12345"
    # Mock arxiv client and PDF download
    mocker.patch("arxiv.Client.results")
    mocker.patch("arxiv.Result.download_pdf")

    # Mock PDF to markdown conversion to happen immediately
    async def mock_convert(paper_id, pdf_path):
        md_path = get_paper_path(paper_id, ".md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Test Paper\nConverted content")
        if paper_id in conversion_statuses:
            status = conversion_statuses[paper_id]
            status.status = "success"
            status.completed_at = datetime.now()
        pdf_path.unlink()  # Cleanup PDF

    mocker.patch("asyncio.to_thread", side_effect=mock_convert)

    # Initial download request
    response = await handle_download({"paper_id": paper_id})
    status = json.loads(response[0].text)
    assert status["status"] in ["converting", "success"]

    # Check final status
    response = await handle_download({"paper_id": paper_id, "check_status": True})
    final_status = json.loads(response[0].text)
    assert final_status["status"] in ["success", "converting"]

    # Verify markdown file exists
    if final_status["status"] == "success":
        assert get_paper_path(paper_id, ".md").exists()


@pytest.mark.asyncio
async def test_download_existing_paper(temp_storage_path):
    """Test downloading a paper that's already available."""
    paper_id = "2103.12345"
    md_path = get_paper_path(paper_id, ".md")

    # Create test markdown file
    md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Existing Paper\nTest content")

    response = await handle_download({"paper_id": paper_id})
    status = json.loads(response[0].text)
    assert status["status"] == "success"


@pytest.mark.asyncio
async def test_download_nonexistent_paper(mocker):
    """Test downloading a paper that doesn't exist."""
    mocker.patch("arxiv.Client.results", side_effect=StopIteration())

    response = await handle_download({"paper_id": "invalid.12345"})
    status = json.loads(response[0].text)
    assert status["status"] == "error"
    assert "not found on arXiv" in status["message"]


@pytest.mark.asyncio
async def test_check_unknown_status():
    """Test checking status of unknown paper."""
    response = await handle_download({"paper_id": "2103.99999", "check_status": True})
    status = json.loads(response[0].text)
    assert status["status"] == "unknown"
