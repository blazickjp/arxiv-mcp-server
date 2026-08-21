"""Force refresh and extractor-version cache tests for download_paper (#175)."""

import json

import pytest

from arxiv_mcp_server.tools.download import (
    EXTRACTOR_VERSION,
    download_tool,
    handle_download,
)


def _write_cached_paper(storage, paper_id, content, extractor_version=None):
    """Write markdown plus a sidecar stamped with an extractor version."""
    (storage / f"{paper_id}.md").write_text(content, encoding="utf-8")
    version = EXTRACTOR_VERSION if extractor_version is None else extractor_version
    payload = {
        "id": paper_id,
        "title": "Cached Paper",
        "authors": [],
        "published": None,
        "extractor_version": version,
    }
    (storage / f"{paper_id}.meta.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _fake_path(storage):
    def fake_path(pid, suffix=".md"):
        return storage / f"{pid}{suffix}"

    return fake_path


def test_download_tool_schema_includes_force():
    """download_paper exposes force and stays a closed schema."""
    props = download_tool.inputSchema["properties"]
    assert "force" in props
    assert props["force"]["type"] == "boolean"
    assert download_tool.inputSchema.get("additionalProperties") is False
    assert "paper_id" in download_tool.inputSchema["required"]


@pytest.mark.asyncio
async def test_force_true_rewrites_cached_paper(temp_storage_path, mocker):
    """force=true re-fetches and overwrites markdown plus sidecar."""
    paper_id = "1706.03762"
    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path",
        side_effect=_fake_path(temp_storage_path),
    )
    _write_cached_paper(temp_storage_path, paper_id, "OLD DIRTY CACHE")

    new_text = "Attention Is All You Need\nFresh extract."
    mock_html = mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=new_text,
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_arxiv_metadata",
        return_value={
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani"],
            "published": "2017-06-12T00:00:00+00:00",
        },
    )
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id, "force": True})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "html"
    assert (temp_storage_path / f"{paper_id}.md").read_text(
        encoding="utf-8"
    ) == new_text
    sidecar = json.loads(
        (temp_storage_path / f"{paper_id}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["extractor_version"] == EXTRACTOR_VERSION
    assert sidecar["title"] == "Attention Is All You Need"
    mock_html.assert_called_once()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_matching_extractor_version_is_cache_hit(temp_storage_path, mocker):
    """A sidecar stamped with the current extractor version skips the network."""
    paper_id = "2103.12345"
    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path",
        side_effect=_fake_path(temp_storage_path),
    )
    _write_cached_paper(
        temp_storage_path,
        paper_id,
        "Fresh enough",
        extractor_version=EXTRACTOR_VERSION,
    )
    mock_html = mocker.patch("arxiv_mcp_server.tools.download._fetch_html_content")
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "cache"
    assert "Fresh enough" in result["content"]
    mock_html.assert_not_called()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_old_extractor_version_refreshes_cache(temp_storage_path, mocker):
    """An older extractor stamp is treated as stale and re-downloaded."""
    paper_id = "2608.18261"
    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path",
        side_effect=_fake_path(temp_storage_path),
    )
    _write_cached_paper(
        temp_storage_path,
        paper_id,
        "pre-fix chrome text",
        extractor_version=EXTRACTOR_VERSION - 1,
    )
    new_text = "Cacheable by Design?\nClean extract."
    mock_html = mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=new_text,
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_arxiv_metadata",
        return_value={
            "title": "Cacheable by Design?",
            "authors": ["Shriniwas Ramesh Suram"],
            "published": "2026-08-20T00:00:00+00:00",
        },
    )
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "html"
    assert (temp_storage_path / f"{paper_id}.md").read_text(
        encoding="utf-8"
    ) == new_text
    sidecar = json.loads(
        (temp_storage_path / f"{paper_id}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["extractor_version"] == EXTRACTOR_VERSION
    mock_html.assert_called_once()
    mock_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_missing_extractor_version_refreshes_cache(temp_storage_path, mocker):
    """A cache written before version stamps is treated as stale."""
    paper_id = "2103.77777"
    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path",
        side_effect=_fake_path(temp_storage_path),
    )
    (temp_storage_path / f"{paper_id}.md").write_text(
        "legacy cache without sidecar", encoding="utf-8"
    )
    new_text = "Rewritten after missing version."
    mock_html = mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=new_text,
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_arxiv_metadata",
        return_value=None,
    )
    mock_pdf = mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["source"] == "html"
    assert (temp_storage_path / f"{paper_id}.md").read_text(
        encoding="utf-8"
    ) == new_text
    sidecar = json.loads(
        (temp_storage_path / f"{paper_id}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["extractor_version"] == EXTRACTOR_VERSION
    mock_html.assert_called_once()
    mock_pdf.assert_not_called()
