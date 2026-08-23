"""Regression: legacy slash-form arXiv IDs must store without FileNotFoundError (#254)."""

import json

import pytest

from arxiv_mcp_server.tools import download as download_module
from arxiv_mcp_server.tools import list_papers as list_papers_module
from arxiv_mcp_server.tools import read_paper as read_module
from arxiv_mcp_server.tools.arxiv_ids import (
    filesystem_arxiv_stem,
    logical_arxiv_id_from_stem,
)
from arxiv_mcp_server.tools.download import (
    EXTRACTOR_VERSION,
    get_paper_path,
    handle_download,
)
from arxiv_mcp_server.tools.list_papers import handle_list_papers
from arxiv_mcp_server.tools.read_paper import handle_read_paper

LEGACY_IDS = ("hep-th/9901001", "quant-ph/0101001", "math-ph/0001001")


@pytest.mark.parametrize(
    "logical, expected_stem",
    [
        ("hep-th/9901001", "hep-th__9901001"),
        ("quant-ph/0101001", "quant-ph__0101001"),
        ("math-ph/0001001", "math-ph__0001001"),
        ("1706.03762", "1706.03762"),
        ("hep-th/9901001v1", "hep-th__9901001v1"),
    ],
)
def test_filesystem_arxiv_stem_flattens_legacy_slash(logical, expected_stem):
    assert filesystem_arxiv_stem(logical) == expected_stem
    assert logical_arxiv_id_from_stem(expected_stem) == logical


def test_get_paper_path_legacy_id_is_flat(temp_storage_path, monkeypatch):
    """get_paper_path must not nest under an uncreated category directory."""
    monkeypatch.setattr(
        download_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    path = get_paper_path("hep-th/9901001", ".md")
    assert path.parent == temp_storage_path.resolve()
    assert path.name == "hep-th__9901001.md"
    # Parent exists so a subsequent write cannot FileNotFoundError.
    assert path.parent.is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_id", LEGACY_IDS)
async def test_download_legacy_slash_id_writes_markdown(
    temp_storage_path, mocker, monkeypatch, paper_id
):
    """download_paper on legacy IDs succeeds and writes a flat storage file."""
    monkeypatch.setattr(
        download_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    monkeypatch.setattr(
        list_papers_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value=f"# Legacy {paper_id}\nBody text for regression.",
    )
    mocker.patch.object(
        download_module,
        "_fetch_arxiv_metadata",
        return_value={
            "title": f"Legacy paper {paper_id}",
            "authors": ["Test Author"],
            "published": "1999-01-01T00:00:00Z",
            "arxiv_version": "v1",
        },
    )
    mocker.patch.object(download_module, "_fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)

    assert result["status"] == "success", result
    assert result["paper_id"] == paper_id
    assert result["source"] == "html"
    assert "Body text for regression." in result["content"]

    flat = temp_storage_path / f"{filesystem_arxiv_stem(paper_id)}.md"
    assert flat.exists()
    # No category subdirectory should have been created.
    category = paper_id.split("/", 1)[0]
    assert not (temp_storage_path / category).exists()

    sidecar = temp_storage_path / f"{filesystem_arxiv_stem(paper_id)}.meta.json"
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["id"] == paper_id
    assert meta["extractor_version"] == EXTRACTOR_VERSION


@pytest.mark.asyncio
async def test_list_and_read_roundtrip_legacy_id(
    temp_storage_path, mocker, monkeypatch
):
    """Listed and read paths resolve the sanitized flat stem back to the slash ID."""
    paper_id = "hep-th/9901001"
    for module in (download_module, list_papers_module, read_module):
        monkeypatch.setattr(
            module.settings,
            "_get_storage_path_from_args",
            lambda: temp_storage_path,
        )
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Roundtrip\nLegacy body",
    )
    mocker.patch.object(
        download_module,
        "_fetch_arxiv_metadata",
        return_value={
            "title": "Roundtrip Legacy",
            "authors": ["A"],
            "published": "1999-01-01T00:00:00Z",
            "arxiv_version": "v1",
        },
    )
    mocker.patch.object(download_module, "_fetch_pdf_content")

    download = await handle_download({"paper_id": paper_id})
    assert json.loads(download[0].text)["status"] == "success"

    listed = json.loads((await handle_list_papers({"compact": True}))[0].text)
    assert paper_id in listed["papers"]

    read = json.loads((await handle_read_paper({"paper_id": paper_id}))[0].text)
    assert read["status"] == "success"
    assert read["paper_id"] == paper_id
    assert "Legacy body" in read["content"]


@pytest.mark.asyncio
async def test_download_oserror_omits_absolute_paths(
    temp_storage_path, mocker, monkeypatch
):
    """Filesystem failures must not leak absolute host paths to the client (#254)."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        download_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    mocker.patch.object(
        download_module,
        "_fetch_html_content",
        return_value="# Body\nContent",
    )
    abs_path = str((temp_storage_path / "hep-th" / "9901001.md").resolve())

    mock_path = MagicMock()
    mock_path.exists.return_value = False
    mock_path.write_text.side_effect = FileNotFoundError(
        2, "No such file or directory", abs_path
    )
    mocker.patch.object(download_module, "get_paper_path", return_value=mock_path)

    response = await handle_download({"paper_id": "hep-th/9901001"})
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    message = result["message"]
    assert abs_path not in message
    assert str(temp_storage_path.resolve()) not in message
    assert "Storage error" in message
