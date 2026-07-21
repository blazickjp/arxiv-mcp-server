"""Tests for safe, bounded arXiv LaTeX source tools."""

from __future__ import annotations

import io
import json
import tarfile
from unittest.mock import MagicMock

import pytest

from arxiv_mcp_server.tools import latex


def _tar_bytes(
    files: dict[str, bytes], *, links: dict[str, str] | None = None
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        for name, target in (links or {}).items():
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            archive.addfile(member)
    return buffer.getvalue()


def _payload(result):
    return json.loads(result[0].text)


def test_extract_tex_files_rejects_path_traversal():
    archive = _tar_bytes({"../secret.tex": b"secret"})

    with pytest.raises(latex.UnsafeSourceArchiveError, match="unsafe path"):
        latex._extract_tex_files(archive)


def test_extract_tex_files_rejects_links():
    archive = _tar_bytes(
        {"main.tex": b"\\documentclass{article}"},
        links={"escape.tex": "../../secret"},
    )

    with pytest.raises(latex.UnsafeSourceArchiveError, match="link"):
        latex._extract_tex_files(archive)


def test_extract_tex_files_rejects_oversized_member(monkeypatch):
    monkeypatch.setattr(latex, "MAX_MEMBER_BYTES", 8)
    archive = _tar_bytes({"main.tex": b"0123456789"})

    with pytest.raises(latex.SourceArchiveLimitError, match="member"):
        latex._extract_tex_files(archive)


def test_extract_tex_files_supports_plain_gzip():
    import gzip

    source = b"\\documentclass{article}\n\\begin{document}\nHello\\end{document}"
    files = latex._extract_tex_files(gzip.compress(source))

    assert files == {"main.tex": source.decode()}


def test_flatten_source_selects_main_document_and_resolves_inputs():
    files = {
        "notes.tex": "scratch",
        "paper.tex": (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\input{sections/intro}\n"
            "\\end{document}\n"
        ),
        "sections/intro.tex": "\\section{Introduction}\nEvidence.",
    }

    flattened, main_file = latex._flatten_source(files)

    assert main_file == "paper.tex"
    assert "\\section{Introduction}" in flattened
    assert "Evidence." in flattened
    assert "\\input{sections/intro}" not in flattened


def test_flatten_source_does_not_follow_unsafe_or_cyclic_inputs():
    files = {
        "main.tex": (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\input{../secret}\n\\input{loop}\n\\end{document}"
        ),
        "loop.tex": "\\input{loop}\nLoop body",
        "../secret.tex": "must not appear",
    }

    flattened, _ = latex._flatten_source(files)

    assert "must not appear" not in flattened
    assert len(flattened) < 1000


def test_parse_sections_returns_stable_hierarchical_ids():
    source = r"""
\section{Introduction}
Intro.
\subsection{Motivation}
Why.
\subsubsection{Prior work}
History.
\section{Results}
Numbers.
"""

    sections = latex._parse_sections(source)

    assert [(s.section_id, s.title) for s in sections] == [
        ("1", "Introduction"),
        ("1.1", "Motivation"),
        ("1.1.1", "Prior work"),
        ("2", "Results"),
    ]
    assert latex._extract_section(source, sections, "1.1").startswith(
        "\\subsection{Motivation}"
    )
    assert "\\section{Results}" not in latex._extract_section(source, sections, "1")


def test_download_archive_aborts_when_stream_exceeds_limit(monkeypatch):
    monkeypatch.setattr(latex, "MAX_ARCHIVE_BYTES", 5)
    response = MagicMock()
    response.headers = {}
    response.raise_for_status = MagicMock()
    response.iter_bytes.return_value = [b"123", b"456"]
    response_cm = MagicMock()
    response_cm.__enter__.return_value = response
    response_cm.__exit__.return_value = False
    client = MagicMock()
    client.stream.return_value = response_cm
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    monkeypatch.setattr(latex.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(
        latex.ARXIV_RATE_LIMITER, "run_sync", lambda operation: operation()
    )

    with pytest.raises(latex.SourceArchiveLimitError, match="compressed"):
        latex._download_source_archive("2401.00001")


def test_tool_schemas_are_closed_and_content_is_bounded():
    assert latex.get_paper_latex_tool.inputSchema["additionalProperties"] is False
    props = latex.get_paper_latex_tool.inputSchema["properties"]
    assert props["max_chars"]["maximum"] == latex.MAX_RETURN_CHARS
    assert (
        latex.get_paper_latex_section_tool.inputSchema["additionalProperties"] is False
    )


@pytest.mark.asyncio
async def test_get_latex_rejects_invalid_id_before_loading(monkeypatch):
    load = MagicMock()
    monkeypatch.setattr(latex, "_load_source", load)

    payload = _payload(await latex.handle_get_paper_latex({"paper_id": "../secret"}))

    assert payload["status"] == "error"
    assert "invalid arXiv ID" in payload["message"]
    load.assert_not_called()


@pytest.mark.asyncio
async def test_get_latex_defaults_to_bounded_content(monkeypatch):
    monkeypatch.setattr(latex, "DEFAULT_MAX_CHARS", 10)
    monkeypatch.setattr(
        latex,
        "_load_source",
        lambda _paper_id: latex.LatexSource(
            content="abcdefghijklmnopqrstuvwxyz", main_file="main.tex", source_files=2
        ),
    )

    payload = _payload(await latex.handle_get_paper_latex({"paper_id": "2401.00001"}))

    assert payload["status"] == "success"
    assert payload["returned_chars"] == 10
    assert payload["next_start"] == 10
    assert payload["is_truncated"] is True
    assert payload["content"].endswith("abcdefghij")
    assert payload["main_file"] == "main.tex"


@pytest.mark.asyncio
async def test_get_latex_honors_explicit_page(monkeypatch):
    monkeypatch.setattr(
        latex,
        "_load_source",
        lambda _paper_id: latex.LatexSource(
            content="abcdefghijklmnopqrstuvwxyz", main_file="paper.tex", source_files=1
        ),
    )

    payload = _payload(
        await latex.handle_get_paper_latex(
            {"paper_id": "2401.00001", "start": 5, "max_chars": 4}
        )
    )

    assert payload["content"].endswith("fghi")
    assert payload["start"] == 5
    assert payload["next_start"] == 9


@pytest.mark.asyncio
async def test_list_latex_sections_is_compact(monkeypatch):
    source = "\\section{Intro}\nA\n\\subsection{Method}\nB"
    monkeypatch.setattr(
        latex,
        "_load_source",
        lambda _paper_id: latex.LatexSource(source, "main.tex", 1),
    )

    payload = _payload(
        await latex.handle_list_paper_latex_sections({"paper_id": "2401.00001"})
    )

    assert payload["status"] == "success"
    assert payload["sections"] == [
        {"id": "1", "level": 1, "title": "Intro"},
        {"id": "1.1", "level": 2, "title": "Method"},
    ]
    assert "content" not in payload


@pytest.mark.asyncio
async def test_get_latex_section_supports_bounded_continuation(monkeypatch):
    source = "\\section{Intro}\nabcdefghij\n\\section{Next}\nnope"
    monkeypatch.setattr(
        latex,
        "_load_source",
        lambda _paper_id: latex.LatexSource(source, "main.tex", 1),
    )

    first = _payload(
        await latex.handle_get_paper_latex_section(
            {"paper_id": "2401.00001", "section_id": "1", "max_chars": 8}
        )
    )
    second = _payload(
        await latex.handle_get_paper_latex_section(
            {
                "paper_id": "2401.00001",
                "section_id": "1",
                "start": first["next_start"],
                "max_chars": 100,
            }
        )
    )

    assert first["is_truncated"] is True
    assert second["is_truncated"] is False
    assert "Next" not in first["content"] + second["content"]


@pytest.mark.asyncio
async def test_get_latex_section_reports_missing_section(monkeypatch):
    monkeypatch.setattr(
        latex,
        "_load_source",
        lambda _paper_id: latex.LatexSource("\\section{Intro}\nText", "main.tex", 1),
    )

    payload = _payload(
        await latex.handle_get_paper_latex_section(
            {"paper_id": "2401.00001", "section_id": "99"}
        )
    )

    assert payload["status"] == "error"
    assert "not found" in payload["message"]


@pytest.mark.asyncio
async def test_server_registers_and_routes_latex_tools(monkeypatch):
    from arxiv_mcp_server import server

    names = {tool.name for tool in await server.list_tools()}
    assert {
        "get_paper_latex",
        "list_paper_latex_sections",
        "get_paper_latex_section",
    } <= names

    monkeypatch.setattr(
        server,
        "handle_get_paper_latex",
        lambda _args: None,
    )

    async def fake_handler(_args):
        from mcp.types import TextContent

        return [TextContent(type="text", text='{"status":"success"}')]

    monkeypatch.setattr(server, "handle_get_paper_latex", fake_handler)
    result = await server.call_tool("get_paper_latex", {"paper_id": "2401.00001"})
    assert json.loads(result[0].text)["status"] == "success"
