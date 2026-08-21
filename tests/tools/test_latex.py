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


def test_safe_member_name_rejects_nul_long_and_deep_paths():
    with pytest.raises(latex.UnsafeSourceArchiveError, match="NUL"):
        latex._safe_member_name("bad\x00name.tex")
    with pytest.raises(latex.SourceArchiveLimitError, match="path length"):
        latex._safe_member_name("a" * (latex.MAX_ARCHIVE_PATH_BYTES + 1) + ".tex")
    deep = "/".join(["d"] * (latex.MAX_ARCHIVE_PATH_DEPTH + 1)) + "/main.tex"
    with pytest.raises(latex.SourceArchiveLimitError, match="path depth"):
        latex._safe_member_name(deep)


def test_extract_tex_files_rejects_duplicate_normalized_paths():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in (("main.tex", b"first"), ("./main.tex", b"second")):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    with pytest.raises(latex.UnsafeSourceArchiveError, match="duplicate"):
        latex._extract_tex_files(buffer.getvalue())


def test_extract_tex_files_rejects_unsupported_special_members():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        fifo = tarfile.TarInfo("pipe")
        fifo.type = tarfile.FIFOTYPE
        archive.addfile(fifo)
        content = b"\\documentclass{article}"
        member = tarfile.TarInfo("main.tex")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))

    with pytest.raises(latex.UnsafeSourceArchiveError, match="unsupported"):
        latex._extract_tex_files(buffer.getvalue())


def test_extract_tex_files_rejects_links():
    archive = _tar_bytes(
        {"main.tex": b"\\documentclass{article}"},
        links={"escape.tex": "../../secret"},
    )

    with pytest.raises(latex.UnsafeSourceArchiveError, match="link"):
        latex._extract_tex_files(archive)


def test_extract_tex_files_allows_archive_root_directory_entry():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        content = b"\\documentclass{article}"
        member = tarfile.TarInfo("./main.tex")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))

    assert latex._extract_tex_files(buffer.getvalue()) == {
        "main.tex": "\\documentclass{article}"
    }


def test_extract_tex_files_rejects_oversized_member(monkeypatch):
    monkeypatch.setattr(latex, "MAX_MEMBER_BYTES", 8)
    archive = _tar_bytes({"main.tex": b"0123456789"})

    with pytest.raises(latex.SourceArchiveLimitError, match="member"):
        latex._extract_tex_files(archive)


def test_extract_tex_files_supports_plain_gzip():
    import gzip

    source = b"\\documentclass{article}\\n\\begin{document}\\nHello\\end{document}"
    files = latex._extract_tex_files(gzip.compress(source))

    assert files == {"main.tex": source.decode()}


def test_load_cached_source_rejects_stale_format_and_oversized_content(
    monkeypatch, tmp_path
):
    cache = tmp_path / "paper.json"
    monkeypatch.setattr(latex, "_cache_path", lambda _paper_id: cache)
    monkeypatch.setattr(latex, "MAX_FLATTENED_CHARS", 10)

    cache.write_text(
        json.dumps(
            {
                "cache_format": latex.CACHE_FORMAT_VERSION - 1,
                "content": "safe",
                "main_file": "main.tex",
                "source_files": 1,
            }
        )
    )
    assert latex._load_cached_source("2401.00001") is None
    assert not cache.exists()

    cache.write_text(
        json.dumps(
            {
                "cache_format": latex.CACHE_FORMAT_VERSION,
                "content": "x" * (latex.MAX_FLATTENED_CHARS + 1),
                "main_file": "main.tex",
                "source_files": 1,
            }
        )
    )
    assert latex._load_cached_source("2401.00001") is None
    assert not cache.exists()


def test_write_cached_source_records_format_version(monkeypatch, tmp_path):
    cache = tmp_path / "paper.json"
    monkeypatch.setattr(latex, "_cache_path", lambda _paper_id: cache)

    latex._write_cached_source(
        "2401.00001", latex.LatexSource("content", "main.tex", 1)
    )

    assert json.loads(cache.read_text())["cache_format"] == latex.CACHE_FORMAT_VERSION


def test_flatten_source_selects_main_document_and_resolves_inputs():
    files = {
        "notes.tex": "scratch",
        "paper.tex": (
            "\\documentclass{article}\\n"
            "\\begin{document}\\n"
            "\\input{sections/intro}\\n"
            "\\end{document}\\n"
        ),
        "sections/intro.tex": "\\section{Introduction}\\nEvidence.",
    }

    flattened, main_file = latex._flatten_source(files)

    assert main_file == "paper.tex"
    assert "\\section{Introduction}" in flattened
    assert "Evidence." in flattened
    assert "\\input{sections/intro}" not in flattened


def test_flatten_source_does_not_follow_unsafe_or_cyclic_inputs():
    files = {
        "main.tex": (
            "\\documentclass{article}\\n\\begin{document}\\n"
            "\\input{../secret}\\n\\input{loop}\\n\\end{document}"
        ),
        "loop.tex": "\\input{loop}\\nLoop body",
        "../secret.tex": "must not appear",
    }

    flattened, _ = latex._flatten_source(files)

    assert "must not appear" not in flattened
    assert len(flattened) < 1000


def test_flatten_source_resolves_two_arg_import_and_subimport():
    files = {
        "main.tex": (
            "\\documentclass{article}\\n"
            "\\begin{document}\\n"
            "\\import{sections/}{intro}\\n"
            "\\end{document}\\n"
        ),
        "sections/intro.tex": ("\\section{Introduction}\\n\\subimport{./}{method}\\n"),
        "sections/method.tex": "\\subsection{Method}\\nDetails.\\n",
    }

    flattened, main_file, unmatched = latex._flatten_source_with_unmatched(files)

    assert main_file == "main.tex"
    assert "\\section{Introduction}" in flattened
    assert "\\subsection{Method}" in flattened
    assert "Details." in flattened
    assert "\\import{sections/}{intro}" not in flattened
    assert "\\subimport{./}{method}" not in flattened
    assert unmatched == ()


def test_flatten_source_resolves_import_from_archive_root():
    files = {
        "nested/paper.tex": (
            "\\documentclass{article}\\n"
            "\\begin{document}\\n"
            "\\import{shared/}{defs}\\n"
            "\\end{document}\\n"
        ),
        "shared/defs.tex": "\\section{Definitions}\\nTerms.",
    }

    flattened, main_file = latex._flatten_source(files)

    assert main_file == "nested/paper.tex"
    assert "\\section{Definitions}" in flattened
    assert "Terms." in flattened


def test_flatten_source_does_not_escape_via_import():
    files = {
        "main.tex": (
            "\\documentclass{article}\\n"
            "\\import{../}{secret}\\n"
            "\\begin{document}\\n\\end{document}\\n"
        ),
        "../secret.tex": "must not appear",
    }

    flattened, _main, unmatched = latex._flatten_source_with_unmatched(files)

    assert "must not appear" not in flattened
    assert any("import" in item for item in unmatched)


def test_parse_sections_returns_stable_hierarchical_ids():
    source = r"""
\\section{Introduction}
Intro.
\\subsection{Motivation}
Why.
\\subsubsection{Prior work}
History.
\\section{Results}
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
