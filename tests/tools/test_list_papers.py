"""Tests for list_papers local metadata."""

import json
import pytest

from arxiv_mcp_server.tools import list_papers as list_papers_module
from arxiv_mcp_server.tools.list_papers import handle_list_papers, save_paper_metadata


@pytest.fixture
def storage(temp_storage_path, mocker):
    """Point list_papers at an isolated storage directory."""
    mocker.patch.object(
        list_papers_module.settings,
        "_get_storage_path_from_args",
        return_value=temp_storage_path,
    )
    return temp_storage_path


def _write_paper(storage, paper_id, markdown="Paper body", metadata=None):
    (storage / f"{paper_id}.md").write_text(markdown, encoding="utf-8")
    if metadata is not None:
        save_paper_metadata(paper_id, **metadata)


@pytest.mark.asyncio
async def test_list_papers_empty(storage):
    """No downloads yet — empty catalog, no error."""
    response = await handle_list_papers({})
    payload = json.loads(response[0].text)
    assert payload == {"total_papers": 0, "papers": []}


@pytest.mark.asyncio
async def test_list_papers_returns_local_metadata(storage):
    """Default list includes id, title, authors, and published from disk."""
    _write_paper(
        storage,
        "1706.03762",
        markdown="# Attention Is All You Need\nBody",
        metadata={
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "published": "2017-06-12T17:57:34Z",
        },
    )
    _write_paper(
        storage,
        "2608.18261",
        markdown="# Another Paper\nBody",
        metadata={
            "title": "Another Paper",
            "authors": ["Ada Lovelace"],
            "published": "2026-08-20T00:00:00Z",
        },
    )

    response = await handle_list_papers({})
    payload = json.loads(response[0].text)

    assert payload["total_papers"] == 2
    by_id = {paper["id"]: paper for paper in payload["papers"]}
    assert by_id["1706.03762"]["id"] == "1706.03762"
    assert by_id["1706.03762"]["title"] == "Attention Is All You Need"
    assert by_id["1706.03762"]["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert by_id["1706.03762"]["published"] == "2017-06-12T17:57:34Z"
    assert by_id["1706.03762"]["arxiv_version"] is None
    assert by_id["1706.03762"]["versioned_id"] is None
    assert by_id["2608.18261"]["authors"] == ["Ada Lovelace"]


@pytest.mark.asyncio
async def test_list_papers_compact_returns_ids_only(storage):
    """compact=true keeps the previous IDs-only response shape."""
    _write_paper(
        storage,
        "1706.03762",
        metadata={
            "title": "Attention Is All You Need",
            "authors": ["A"],
            "published": "2017",
        },
    )

    response = await handle_list_papers({"compact": True})
    payload = json.loads(response[0].text)

    assert payload["total_papers"] == 1
    assert payload["papers"] == ["1706.03762"]


@pytest.mark.asyncio
async def test_list_papers_uses_markdown_title_without_network(storage, mocker):
    """Already-downloaded papers without a sidecar still stay local."""
    _write_paper(storage, "2401.12345", markdown="# Cached Title\nMore text")
    httpx_get = mocker.patch("httpx.AsyncClient")
    arxiv_search = mocker.patch(
        "arxiv.Search", side_effect=AssertionError("live fetch")
    )

    response = await handle_list_papers({})
    payload = json.loads(response[0].text)

    assert payload["papers"] == [
        {
            "id": "2401.12345",
            "title": "Cached Title",
            "authors": [],
            "published": None,
            "arxiv_version": None,
            "versioned_id": None,
        }
    ]
    httpx_get.assert_not_called()
    arxiv_search.assert_not_called()


@pytest.mark.asyncio
async def test_list_papers_ignores_unreadable_sidecar(storage):
    """Corrupt sidecar must not fail the listing."""
    _write_paper(storage, "2401.00001", markdown="Fallback Title\nBody")
    (storage / "2401.00001.meta.json").write_text("{not-json", encoding="utf-8")

    response = await handle_list_papers({})
    payload = json.loads(response[0].text)

    assert payload["papers"][0]["id"] == "2401.00001"
    assert payload["papers"][0]["title"] == "Fallback Title"
