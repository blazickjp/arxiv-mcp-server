"""Tests for direct arXiv PDF downloads."""

import json
from unittest.mock import MagicMock

import pytest

from arxiv_mcp_server.tools import download as download_module
from arxiv_mcp_server.tools.download import (
    PaperDownloadError,
    _download_arxiv_pdf_to_path,
    _normalize_filename,
    _resolve_output_path,
    handle_download,
)


def _mock_streaming_client(mocker, chunks: list[bytes], status_error=None):
    stream_response = MagicMock()
    stream_response.raise_for_status = MagicMock(side_effect=status_error)
    stream_response.iter_bytes.return_value = chunks

    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = stream_response
    stream_cm.__exit__.return_value = False

    http_client = MagicMock()
    http_client.stream.return_value = stream_cm
    http_client.__enter__.return_value = http_client
    http_client.__exit__.return_value = False

    mocker.patch.object(download_module.httpx, "Client", return_value=http_client)
    return http_client


def test_download_arxiv_pdf_streams_and_validates_pdf(temp_storage_path, mocker):
    """PDF bytes are streamed from arxiv.org/pdf/{paper_id} and saved atomically."""
    http_client = _mock_streaming_client(
        mocker,
        [b"%PDF-", b"body"],
    )
    dest = temp_storage_path / "paper.pdf"

    pdf_url = _download_arxiv_pdf_to_path("2103.00000", dest)

    assert pdf_url == "https://arxiv.org/pdf/2103.00000"
    assert dest.read_bytes() == b"%PDF-body"
    http_client.stream.assert_called_once()
    assert http_client.stream.call_args[0][0] == "GET"
    assert http_client.stream.call_args[0][1] == pdf_url
    assert not (temp_storage_path / ".paper.pdf.tmp").exists()


def test_download_arxiv_pdf_rejects_non_pdf_response(temp_storage_path, mocker):
    """A successful HTTP response must still look like a PDF on disk."""
    _mock_streaming_client(mocker, [b"<html>not found</html>"])
    dest = temp_storage_path / "paper.pdf"

    with pytest.raises(PaperDownloadError, match="not a valid PDF"):
        _download_arxiv_pdf_to_path("2103.00000", dest)

    assert not dest.exists()
    assert (temp_storage_path / ".paper.pdf.bad").exists()


def test_download_arxiv_pdf_rejects_invalid_arxiv_id(temp_storage_path):
    """Invalid paper IDs fail before a network request is attempted."""
    with pytest.raises(PaperDownloadError, match="Invalid arXiv paper ID"):
        _download_arxiv_pdf_to_path("not a paper", temp_storage_path / "paper.pdf")


def test_download_arxiv_pdf_accepts_old_style_subject_ids(temp_storage_path, mocker):
    """Old arXiv IDs with subject classes are valid PDF paths."""
    _mock_streaming_client(mocker, [b"%PDF-", b"body"])
    dest = temp_storage_path / "paper.pdf"

    pdf_url = _download_arxiv_pdf_to_path("math.AG/0601001", dest)

    assert pdf_url == "https://arxiv.org/pdf/math.AG/0601001"
    assert dest.read_bytes() == b"%PDF-body"


def test_normalize_filename_appends_pdf_and_rejects_paths():
    assert _normalize_filename("custom-name", "2103.00000") == "custom-name.pdf"
    assert _normalize_filename("custom-name.pdf", "2103.00000") == "custom-name.pdf"
    assert _normalize_filename(None, "hep-ph/9901234") == "hep-ph_9901234.pdf"

    with pytest.raises(ValueError, match="file name only"):
        _normalize_filename("nested/paper.pdf", "2103.00000")
    with pytest.raises(ValueError, match="filename must be a string"):
        _normalize_filename(123, "2103.00000")  # type: ignore[arg-type]


def test_resolve_output_path_uses_requested_directory(temp_storage_path):
    output_path = _resolve_output_path(
        "2103.00000",
        output_dir=str(temp_storage_path / "downloads"),
        filename="named",
    )

    assert output_path == temp_storage_path / "downloads" / "named.pdf"
    assert output_path.parent.exists()


@pytest.mark.asyncio
async def test_handle_download_returns_local_pdf_details(temp_storage_path, mocker):
    """The MCP handler returns metadata for the saved PDF, not parsed paper text."""

    def fake_download(_paper_id, path):
        path.write_bytes(b"%PDF-body")
        return f"https://arxiv.org/pdf/{_paper_id}"

    mocker.patch.object(
        download_module,
        "_download_arxiv_pdf_to_path",
        side_effect=fake_download,
    )

    response = await handle_download(
        {
            "paper_id": "2103.12345",
            "output_dir": str(temp_storage_path),
            "filename": "attention",
        }
    )
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    assert result["message"] == "PDF downloaded from arXiv"
    assert result["paper_id"] == "2103.12345"
    assert result["pdf_url"] == "https://arxiv.org/pdf/2103.12345"
    assert result["path"] == str(temp_storage_path / "attention.pdf")
    assert result["filename"] == "attention.pdf"
    assert result["size_bytes"] == len(b"%PDF-body")


@pytest.mark.asyncio
async def test_handle_download_waits_for_shared_arxiv_slot(temp_storage_path, mocker):
    """Downloads run the whole network operation inside the shared arXiv slot."""
    calls = []

    async def fake_run_with_slot(func):
        calls.append("slot-start")
        result = func()
        calls.append("slot-end")
        return result

    slot_mock = mocker.patch.object(
        download_module, "run_sync_with_arxiv_slot", side_effect=fake_run_with_slot
    )

    def fake_download(_paper_id, path):
        calls.append("download")
        path.write_bytes(b"%PDF-body")
        return f"https://arxiv.org/pdf/{_paper_id}"

    download_mock = mocker.patch.object(
        download_module,
        "_download_arxiv_pdf_to_path",
        side_effect=fake_download,
    )

    response = await handle_download(
        {"paper_id": "2103.12345", "output_dir": str(temp_storage_path)}
    )
    result = json.loads(response[0].text)

    assert result["status"] == "success"
    slot_mock.assert_awaited_once()
    download_mock.assert_called_once()
    assert calls == ["slot-start", "download", "slot-end"]


@pytest.mark.asyncio
async def test_handle_download_reports_errors(temp_storage_path, mocker):
    mocker.patch.object(
        download_module,
        "_download_arxiv_pdf_to_path",
        side_effect=PaperDownloadError("Paper 2103.00000 not found on arXiv"),
    )

    response = await handle_download(
        {"paper_id": "2103.00000", "output_dir": str(temp_storage_path)}
    )
    result = json.loads(response[0].text)

    assert result["status"] == "error"
    assert "not found on arXiv" in result["message"]
