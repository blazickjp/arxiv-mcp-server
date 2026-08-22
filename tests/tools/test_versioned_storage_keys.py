"""Versioned download keys resolve via bare ID (#202)."""

import json

import pytest

from arxiv_mcp_server.tools import download as download_module
from arxiv_mcp_server.tools import list_papers as list_papers_module
from arxiv_mcp_server.tools import read_paper as read_module
from arxiv_mcp_server.tools.download import EXTRACTOR_VERSION, handle_download
from arxiv_mcp_server.tools.list_papers import handle_list_papers, save_paper_metadata
from arxiv_mcp_server.tools.read_paper import handle_read_paper


def _patch_download_path(mocker, storage):
    mocker.patch.object(
        download_module,
        "get_paper_path",
        side_effect=lambda pid, suffix=".md": storage / f"{pid}{suffix}",
    )


def _patch_list_storage(mocker, storage):
    mocker.patch.object(
        list_papers_module.settings,
        "_get_storage_path_from_args",
        return_value=storage,
    )


def _patch_read_storage(monkeypatch, storage):
    monkeypatch.setattr(
        read_module.settings,
        "_get_storage_path_from_args",
        lambda: storage,
    )


@pytest.mark.asyncio
async def test_versioned_download_stores_under_bare_id(temp_storage_path, mocker):
    """download_paper('…v7') writes bare-ID markdown + sidecar version."""
    _patch_download_path(mocker, temp_storage_path)
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Attention\nBody from v7",
    )
    mocker.patch.object(
        download_module,
        "_fetch_arxiv_metadata",
        return_value={
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani"],
            "published": "2017-06-12T00:00:00Z",
            "arxiv_version": "v7",
        },
    )
    mocker.patch.object(download_module, "_fetch_pdf_content")

    response = await handle_download({"paper_id": "1706.03762v7"})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["paper_id"] == "1706.03762"
    assert (temp_storage_path / "1706.03762.md").exists()
    assert not (temp_storage_path / "1706.03762v7.md").exists()

    sidecar = json.loads(
        (temp_storage_path / "1706.03762.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["id"] == "1706.03762"
    assert sidecar["arxiv_version"] == "v7"
    assert sidecar["extractor_version"] == EXTRACTOR_VERSION


@pytest.mark.asyncio
async def test_bare_read_finds_versioned_download(
    temp_storage_path, mocker, monkeypatch
):
    """After a versioned download, read_paper(bare) succeeds."""
    _patch_download_path(mocker, temp_storage_path)
    _patch_list_storage(mocker, temp_storage_path)
    _patch_read_storage(monkeypatch, temp_storage_path)
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Attention\nTransformer body",
    )
    mocker.patch.object(
        download_module,
        "_fetch_arxiv_metadata",
        return_value={
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani"],
            "published": "2017-06-12T00:00:00Z",
            "arxiv_version": "v7",
        },
    )
    mocker.patch.object(download_module, "_fetch_pdf_content")

    download = await handle_download({"paper_id": "1706.03762v7"})
    assert json.loads(download[0].text)["status"] == "success"

    read = await handle_read_paper({"paper_id": "1706.03762"})
    payload = json.loads(read[0].text)
    assert payload["status"] == "success"
    assert payload["paper_id"] == "1706.03762"
    assert "Transformer body" in payload["content"]


@pytest.mark.asyncio
async def test_list_papers_dedupes_bare_and_versioned_keys(temp_storage_path, mocker):
    """Legacy dual keys for one paper collapse to a single bare ID."""
    _patch_list_storage(mocker, temp_storage_path)
    (temp_storage_path / "1706.03762v7.md").write_text("legacy v7", encoding="utf-8")
    (temp_storage_path / "1706.03762.md").write_text("bare copy", encoding="utf-8")
    save_paper_metadata(
        "1706.03762",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani"],
        published="2017-06-12T00:00:00Z",
        arxiv_version="v7",
    )

    response = await handle_list_papers({"compact": True})
    payload = json.loads(response[0].text)

    assert payload["total_papers"] == 1
    assert payload["papers"] == ["1706.03762"]


@pytest.mark.asyncio
async def test_list_papers_prefers_latest_legacy_version_only(
    temp_storage_path, mocker
):
    """When only versioned legacy files exist, list still shows one bare ID."""
    _patch_list_storage(mocker, temp_storage_path)
    (temp_storage_path / "1706.03762v3.md").write_text("v3", encoding="utf-8")
    (temp_storage_path / "1706.03762v7.md").write_text("v7", encoding="utf-8")

    response = await handle_list_papers({})
    payload = json.loads(response[0].text)

    assert payload["total_papers"] == 1
    assert payload["papers"][0]["id"] == "1706.03762"
    assert payload["papers"][0]["title"] == "v7"


@pytest.mark.asyncio
async def test_bare_read_resolves_legacy_versioned_only_storage(
    temp_storage_path, mocker, monkeypatch
):
    """Bare read works against a pre-fix versioned filename."""
    _patch_list_storage(mocker, temp_storage_path)
    _patch_read_storage(monkeypatch, temp_storage_path)
    (temp_storage_path / "1706.03762v7.md").write_text(
        "legacy only content", encoding="utf-8"
    )

    read = await handle_read_paper({"paper_id": "1706.03762"})
    payload = json.loads(read[0].text)
    assert payload["status"] == "success"
    assert payload["paper_id"] == "1706.03762"
    assert "legacy only content" in payload["content"]


@pytest.mark.asyncio
async def test_versioned_download_cleans_legacy_alias(temp_storage_path, mocker):
    """Writing the bare key removes a leftover versioned alias."""
    _patch_download_path(mocker, temp_storage_path)
    (temp_storage_path / "1706.03762v7.md").write_text("old", encoding="utf-8")
    (temp_storage_path / "1706.03762v7.meta.json").write_text("{}", encoding="utf-8")
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Fresh\nBody",
    )
    mocker.patch.object(
        download_module,
        "_fetch_arxiv_metadata",
        return_value={
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani"],
            "published": "2017-06-12T00:00:00Z",
            "arxiv_version": "v7",
        },
    )
    mocker.patch.object(download_module, "_fetch_pdf_content")

    await handle_download({"paper_id": "1706.03762v7"})

    assert (temp_storage_path / "1706.03762.md").exists()
    assert not (temp_storage_path / "1706.03762v7.md").exists()
    assert not (temp_storage_path / "1706.03762v7.meta.json").exists()
