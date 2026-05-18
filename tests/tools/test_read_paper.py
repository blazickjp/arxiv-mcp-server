"""Tests for reading downloaded papers."""

import json

import pytest

from arxiv_mcp_server.tools import read_paper as read_module
from arxiv_mcp_server.tools.read_paper import handle_read_paper


@pytest.mark.asyncio
async def test_read_paper_supports_content_pagination(temp_storage_path, monkeypatch):
    """Large papers can be retrieved in bounded chunks instead of one huge payload."""
    monkeypatch.setattr(
        read_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    paper_id = "2505.13525"
    content = "abcdefghijklmnopqrstuvwxyz"
    (temp_storage_path / f"{paper_id}.md").write_text(content, encoding="utf-8")

    response = await handle_read_paper(
        {"paper_id": paper_id, "start": 5, "max_chars": 10}
    )
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["paper_id"] == paper_id
    assert result["content_length"] == len(content)
    assert result["start"] == 5
    assert result["returned_chars"] == 10
    assert result["next_start"] == 15
    assert result["is_truncated"] is True
    chunk = result["content"].split("\n\n", 1)[1]
    assert chunk == "fghijklmno"


@pytest.mark.asyncio
async def test_read_paper_reports_end_of_content_for_final_chunk(
    temp_storage_path, monkeypatch
):
    """Final chunks should make it obvious that there is no hidden continuation."""
    monkeypatch.setattr(
        read_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    paper_id = "2505.13525"
    content = "abcdefghijklmnopqrstuvwxyz"
    (temp_storage_path / f"{paper_id}.md").write_text(content, encoding="utf-8")

    response = await handle_read_paper(
        {"paper_id": paper_id, "start": 20, "max_chars": 20}
    )
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["returned_chars"] == 6
    assert result["next_start"] is None
    assert result["is_truncated"] is False
    assert result["content"].endswith("uvwxyz")
