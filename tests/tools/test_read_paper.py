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
    assert result["content"] == "fghijklmno"
    assert "content_warning" not in result  # start>0: no banner (#215)
    assert "UNTRUSTED" not in result["content"]


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


@pytest.mark.asyncio
async def test_read_paper_defaults_to_bounded_content(temp_storage_path, monkeypatch):
    """Omitting max_chars must not return an unbounded long paper (#127)."""
    from arxiv_mcp_server.tools.content import DEFAULT_MAX_CHARS

    monkeypatch.setattr(
        read_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    paper_id = "2505.13525"
    content = "A" * (DEFAULT_MAX_CHARS + 4_000)
    (temp_storage_path / f"{paper_id}.md").write_text(content, encoding="utf-8")

    response = await handle_read_paper({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["content_length"] == len(content)
    assert result["returned_chars"] == DEFAULT_MAX_CHARS
    assert result["next_start"] == DEFAULT_MAX_CHARS
    assert result["is_truncated"] is True
    assert "next_start" in result["next_retrieval"]
    assert len(result["content"]) == DEFAULT_MAX_CHARS
    assert "UNTRUSTED EXTERNAL CONTENT" in result["content_warning"]
    assert "UNTRUSTED" not in result["content"]


@pytest.mark.asyncio
async def test_read_paper_short_paper_unchanged_under_default(
    temp_storage_path, monkeypatch
):
    monkeypatch.setattr(
        read_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    paper_id = "2505.13525"
    content = "tiny"
    (temp_storage_path / f"{paper_id}.md").write_text(content, encoding="utf-8")

    response = await handle_read_paper({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["is_truncated"] is False
    assert result["returned_chars"] == len(content)
    assert result["next_start"] is None
    assert result["content"].endswith("tiny")


@pytest.mark.asyncio
async def test_read_paper_return_full_text_opt_in(temp_storage_path, monkeypatch):
    from arxiv_mcp_server.tools.content import DEFAULT_MAX_CHARS

    monkeypatch.setattr(
        read_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    paper_id = "2505.13525"
    content = "B" * (DEFAULT_MAX_CHARS + 3_000)
    (temp_storage_path / f"{paper_id}.md").write_text(content, encoding="utf-8")

    response = await handle_read_paper({"paper_id": paper_id, "return_full_text": True})
    result = json.loads(response[0].text)

    assert result["is_truncated"] is False
    assert result["returned_chars"] == len(content)
    assert result["next_start"] is None
    assert result["content"] == content
    assert "UNTRUSTED EXTERNAL CONTENT" in result["content_warning"]


@pytest.mark.asyncio
async def test_read_paper_invalid_offset_clamps_to_end(temp_storage_path, monkeypatch):
    monkeypatch.setattr(
        read_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    paper_id = "2505.13525"
    content = "abcdefghij"
    (temp_storage_path / f"{paper_id}.md").write_text(content, encoding="utf-8")

    response = await handle_read_paper(
        {"paper_id": paper_id, "start": 999, "max_chars": 50}
    )
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["start"] == len(content)
    assert result["returned_chars"] == 0
    assert result["is_truncated"] is False
    assert result["next_start"] is None
