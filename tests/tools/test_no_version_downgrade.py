"""Bare-ID cache must not silently downgrade versions (#206)."""

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


def _seed_versioned_cache(storage, version, body):
    (storage / "1706.03762.md").write_text(body, encoding="utf-8")
    save_paper_metadata(
        "1706.03762",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani"],
        published="2017-06-12T00:00:00Z",
        extractor_version=EXTRACTOR_VERSION,
        arxiv_version=version,
        path=storage / "1706.03762.meta.json",
    )


@pytest.mark.asyncio
async def test_older_version_without_force_does_not_downgrade(
    temp_storage_path, mocker
):
    """v7 then v1 without force keeps v7 content and reports refusal."""
    _patch_download_path(mocker, temp_storage_path)
    _seed_versioned_cache(temp_storage_path, "v7", "# Attention\nBLEU 41.8 from v7")

    mock_html = mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Attention\nBLEU 41.0 from v1",
    )
    mock_pdf = mocker.patch.object(download_module, "_fetch_pdf_content")

    response = await handle_download({"paper_id": "1706.03762v1"})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "cache"
    assert result["downgrade_refused"] is True
    assert result["requested_version"] == "v1"
    assert result["arxiv_version"] == "v7"
    assert result["versioned_id"] == "1706.03762v7"
    assert result["paper_id"] == "1706.03762"
    assert "BLEU 41.8 from v7" in result["content"]
    assert "41.0 from v1" not in result["content"]
    assert "UNTRUSTED EXTERNAL CONTENT" in result["content_warning"]
    assert len(result["content_warning"]) < 80
    assert "UNTRUSTED" not in result["content"]
    assert (temp_storage_path / "1706.03762.md").read_text(
        encoding="utf-8"
    ) == "# Attention\nBLEU 41.8 from v7"
    sidecar = json.loads(
        (temp_storage_path / "1706.03762.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["arxiv_version"] == "v7"
    mock_html.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_older_version_with_force_may_replace(temp_storage_path, mocker):
    """force=true may replace a newer bare-ID cache with an older version."""
    _patch_download_path(mocker, temp_storage_path)
    _seed_versioned_cache(temp_storage_path, "v7", "# Attention\nBLEU 41.8 from v7")

    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Attention\nBLEU 41.0 from v1",
    )
    mocker.patch.object(
        download_module,
        "_fetch_arxiv_metadata",
        return_value={
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani"],
            "published": "2017-06-12T00:00:00Z",
            "arxiv_version": "v1",
        },
    )
    mocker.patch.object(download_module, "_fetch_pdf_content")

    response = await handle_download({"paper_id": "1706.03762v1", "force": True})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "html"
    assert result.get("downgrade_refused") is not True
    assert result["arxiv_version"] == "v1"
    assert result["versioned_id"] == "1706.03762v1"
    assert "BLEU 41.0 from v1" in result["content"]
    sidecar = json.loads(
        (temp_storage_path / "1706.03762.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["arxiv_version"] == "v1"
    assert "41.0 from v1" in (temp_storage_path / "1706.03762.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_newer_version_without_force_may_upgrade(temp_storage_path, mocker):
    """Requesting a newer version replaces an older bare-ID cache without force."""
    _patch_download_path(mocker, temp_storage_path)
    _seed_versioned_cache(temp_storage_path, "v1", "# Attention\nold v1 body")

    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Attention\nupgraded v7 body",
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
    assert result["source"] == "html"
    assert result["arxiv_version"] == "v7"
    assert result["versioned_id"] == "1706.03762v7"
    assert "upgraded v7 body" in result["content"]
    sidecar = json.loads(
        (temp_storage_path / "1706.03762.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["arxiv_version"] == "v7"


@pytest.mark.asyncio
async def test_bare_download_cache_hit_discloses_stored_version(
    temp_storage_path, mocker
):
    """Bare download with force=false serves the latest stored version and echoes it."""
    _patch_download_path(mocker, temp_storage_path)
    _seed_versioned_cache(temp_storage_path, "v7", "# Attention\nstored v7")

    mock_html = mocker.patch.object(download_module, "_fetch_html_content")
    mock_pdf = mocker.patch.object(download_module, "_fetch_pdf_content")

    response = await handle_download({"paper_id": "1706.03762"})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "cache"
    assert result["arxiv_version"] == "v7"
    assert result["versioned_id"] == "1706.03762v7"
    assert "stored v7" in result["content"]
    mock_html.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_list_and_read_include_version(temp_storage_path, mocker, monkeypatch):
    """list_papers and read_paper echo arxiv_version / versioned_id."""
    _patch_list_storage(mocker, temp_storage_path)
    _patch_read_storage(monkeypatch, temp_storage_path)
    _seed_versioned_cache(temp_storage_path, "v7", "# Attention\nbody")

    listed = json.loads((await handle_list_papers({}))[0].text)
    assert listed["total_papers"] == 1
    paper = listed["papers"][0]
    assert paper["id"] == "1706.03762"
    assert paper["arxiv_version"] == "v7"
    assert paper["versioned_id"] == "1706.03762v7"

    read = json.loads((await handle_read_paper({"paper_id": "1706.03762"}))[0].text)
    assert read["status"] == "success"
    assert read["arxiv_version"] == "v7"
    assert read["versioned_id"] == "1706.03762v7"
