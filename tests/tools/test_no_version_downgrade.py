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
async def test_html_download_with_metadata_prevents_downgrade(
    temp_storage_path, mocker
):
    """HTML download with metadata (from _fetch_arxiv_metadata) prevents downgrade.

    Regression test for issue #206: unversioned HTML downloads should parse
    arxiv_version from the metadata feed's entry id, so a subsequent request
    for an older version refuses downgrade rather than silently overwriting.
    """
    _patch_download_path(mocker, temp_storage_path)

    # Mock HTML fetch to return content
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Test Paper v5\nThis is version 5 content.",
    )

    # Mock metadata fetch to return feed with v5 in the entry id
    feed_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/2404.19756v5</id>
  <title>Test Paper</title>
  <summary>Test summary</summary>
  <published>2024-04-30T00:00:00Z</published>
  <author><name>Test Author</name></author>
  <arxiv:primary_category term="cs.LG"/>
</entry>
</feed>"""

    def mock_get_with_feed(url, **kwargs):
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.iter_content = lambda chunk_size=None, decode_unicode=False: [
            feed_xml
        ]
        mock_response.raise_for_status = lambda: None
        mock_response.close = lambda: None
        mock_response.raw = mocker.Mock()
        return mock_response

    mocker.patch("requests.get", side_effect=mock_get_with_feed)
    mock_limiter = mocker.patch.object(download_module, "ARXIV_RATE_LIMITER")
    mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
    mock_limiter.seconds_until_next_slot.return_value = 0.0
    mocker.patch("time.monotonic", return_value=1000.0)

    # First download: unversioned request, should get v5 from metadata
    response1 = await handle_download({"paper_id": "2404.19756", "force": True})
    result1 = json.loads(response1[0].text)

    assert result1["status"] == "success"
    assert result1["arxiv_version"] == "v5"
    assert result1["versioned_id"] == "2404.19756v5"

    # Check sidecar has arxiv_version
    sidecar = json.loads(
        (temp_storage_path / "2404.19756.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["arxiv_version"] == "v5"

    # Mock HTML fetch for v1 (older version)
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Test Paper v1\nThis is version 1 content.",
    )

    # Mock metadata fetch to return v1
    feed_xml_v1 = feed_xml.replace(b"2404.19756v5", b"2404.19756v1")

    def mock_get_with_feed_v1(url, **kwargs):
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.iter_content = lambda chunk_size=None, decode_unicode=False: [
            feed_xml_v1
        ]
        mock_response.raise_for_status = lambda: None
        mock_response.close = lambda: None
        mock_response.raw = mocker.Mock()
        return mock_response

    mocker.patch("requests.get", side_effect=mock_get_with_feed_v1)

    # Second download: request v1 without force, should refuse downgrade
    response2 = await handle_download({"paper_id": "2404.19756v1"})
    result2 = json.loads(response2[0].text)

    assert result2["status"] == "success"
    assert result2["source"] == "cache"
    assert result2["downgrade_refused"] is True
    assert result2["requested_version"] == "v1"
    assert result2["arxiv_version"] == "v5"
    assert result2["versioned_id"] == "2404.19756v5"
    # Content should still be v5, not v1
    assert "version 5 content" in result2["content"]
    assert "version 1 content" not in result2["content"]

    # Sidecar should still have v5
    sidecar_after = json.loads(
        (temp_storage_path / "2404.19756.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar_after["arxiv_version"] == "v5"


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
