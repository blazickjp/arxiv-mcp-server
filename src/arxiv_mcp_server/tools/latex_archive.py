"""Safe archive download and TeX-member extraction for arXiv sources."""

from __future__ import annotations

import gzip
import io
from pathlib import PurePosixPath
import posixpath
import tarfile

import httpx

from ..arxiv_api import ARXIV_RATE_LIMITER
from ..config import Settings

settings = Settings()

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2_000
MAX_ARCHIVE_PATH_BYTES = 512
MAX_ARCHIVE_PATH_DEPTH = 20
MAX_MEMBER_BYTES = 10 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_TEX_FILES = 500
MAX_TOTAL_TEX_BYTES = 50 * 1024 * 1024


class LatexSourceError(RuntimeError):
    """Base error for unavailable or invalid LaTeX source."""


class UnsafeSourceArchiveError(LatexSourceError):
    """The source archive contains unsafe paths or links."""


class SourceArchiveLimitError(LatexSourceError):
    """The compressed or expanded source exceeds a safety bound."""


def _download_source_archive(paper_id: str) -> bytes:
    """Download one arXiv e-print while enforcing a compressed-size limit."""

    def operation() -> bytes:
        timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
        headers = {
            "User-Agent": (
                f"{settings.APP_NAME}/{settings.APP_VERSION} "
                "(https://github.com/blazickjp/arxiv-mcp-server; research tool)"
            )
        }
        url = f"https://arxiv.org/e-print/{paper_id}"
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers=headers
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared:
                    try:
                        if int(declared) > MAX_ARCHIVE_BYTES:
                            raise SourceArchiveLimitError(
                                "LaTeX source compressed archive exceeds safety limit"
                            )
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_bytes(chunk_size=256 * 1024):
                    received += len(chunk)
                    if received > MAX_ARCHIVE_BYTES:
                        raise SourceArchiveLimitError(
                            "LaTeX source compressed archive exceeds safety limit"
                        )
                    chunks.append(chunk)
        return b"".join(chunks)

    return ARXIV_RATE_LIMITER.run_sync(operation)


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    if "\x00" in normalized:
        raise UnsafeSourceArchiveError(f"NUL byte in source archive path: {name!r}")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.rstrip("/")
    if len(normalized.encode("utf-8")) > MAX_ARCHIVE_PATH_BYTES:
        raise SourceArchiveLimitError(
            f"source archive path length exceeds limit: {name}"
        )
    raw_parts = normalized.split("/")
    if len(raw_parts) > MAX_ARCHIVE_PATH_DEPTH:
        raise SourceArchiveLimitError(
            f"source archive path depth exceeds limit: {name}"
        )
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise UnsafeSourceArchiveError(f"unsafe path in source archive: {name}")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.name:
        raise UnsafeSourceArchiveError(f"unsafe path in source archive: {name}")
    return posixpath.normpath(normalized)


def _read_plain_gzip(data: bytes) -> dict[str, str]:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            content = compressed.read(MAX_MEMBER_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise LatexSourceError(
            "arXiv response is not a supported source archive"
        ) from exc
    if len(content) > MAX_MEMBER_BYTES:
        raise SourceArchiveLimitError("plain gzip source member exceeds safety limit")
    text = content.decode("utf-8", errors="replace")
    if "\\documentclass" not in text and "\\documentstyle" not in text:
        raise LatexSourceError(
            "arXiv source does not contain a recognizable TeX document"
        )
    return {"main.tex": text}


def _extract_tex_files(data: bytes) -> dict[str, str]:
    """Stream TeX members without extracting archive paths to the filesystem."""
    member_count = 0
    files: dict[str, str] = {}
    normalized_members: set[str] = set()
    total_uncompressed = 0
    total_tex = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r|*")
    except tarfile.ReadError:
        return _read_plain_gzip(data)

    try:
        with archive:
            for member in archive:
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBERS:
                    raise SourceArchiveLimitError(
                        "source archive contains too many members"
                    )
                if member.issym() or member.islnk():
                    raise UnsafeSourceArchiveError(
                        f"link entry is not allowed in source archive: {member.name}"
                    )
                normalized_name = member.name.replace("\\", "/")
                if member.isdir() and posixpath.normpath(normalized_name) == ".":
                    # Real arXiv archives may contain an explicit root directory entry.
                    continue
                safe_name = _safe_member_name(member.name)
                if safe_name in normalized_members:
                    raise UnsafeSourceArchiveError(
                        f"duplicate normalized path in source archive: {safe_name}"
                    )
                normalized_members.add(safe_name)
                if not member.isfile() and not member.isdir():
                    raise UnsafeSourceArchiveError(
                        f"unsupported member type in source archive: {member.name}"
                    )
                if member.isdir():
                    continue
                if member.size < 0 or member.size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise SourceArchiveLimitError(
                        f"source archive member exceeds expanded safety limit: {member.name}"
                    )
                total_uncompressed += member.size
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise SourceArchiveLimitError(
                        "source archive expanded size exceeds safety limit"
                    )
                if not safe_name.lower().endswith(".tex"):
                    continue
                if member.size > MAX_MEMBER_BYTES:
                    raise SourceArchiveLimitError(
                        f"TeX source member exceeds safety limit: {member.name}"
                    )
                if len(files) >= MAX_TEX_FILES:
                    raise SourceArchiveLimitError(
                        "source archive contains too many TeX files"
                    )
                total_tex += member.size
                if total_tex > MAX_TOTAL_TEX_BYTES:
                    raise SourceArchiveLimitError(
                        "total TeX source exceeds safety limit"
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    raise LatexSourceError(
                        f"could not read TeX source member: {member.name}"
                    )
                raw = stream.read(MAX_MEMBER_BYTES + 1)
                if len(raw) > MAX_MEMBER_BYTES:
                    raise SourceArchiveLimitError(
                        f"source archive member exceeds safety limit: {member.name}"
                    )
                files[safe_name] = raw.decode("utf-8", errors="replace")
    except tarfile.ReadError as exc:
        if member_count == 0:
            return _read_plain_gzip(data)
        raise LatexSourceError(
            "arXiv source archive is malformed or truncated"
        ) from exc
    if not files:
        raise LatexSourceError("arXiv source archive contains no TeX files")
    return files


def _main_file_score(name: str, content: str) -> tuple[int, int, str]:
    score = 0
    if "\\documentclass" in content or "\\documentstyle" in content:
        score += 100
    if "\\begin{document}" in content:
        score += 50
    if PurePosixPath(name).stem.lower() in {"main", "paper", "article", "manuscript"}:
        score += 20
    return score, len(content), name


def _safe_member_candidate(*parts: str) -> str | None:
    """Join path parts into an archive member name, or None if unsafe."""
    pieces: list[str] = []
    for part in parts:
        cleaned = part.strip().replace("\\", "/")
        if cleaned.startswith("/"):
            return None
        if cleaned:
            pieces.append(cleaned)
    if not pieces:
        return None
    candidate = posixpath.normpath(posixpath.join(*pieces))
    if candidate in {".", ".."} or candidate.startswith("../"):
        return None
    if any(part in {"", ".", ".."} for part in candidate.split("/")):
        return None
    if not PurePosixPath(candidate).suffix:
        candidate += ".tex"
    return candidate
