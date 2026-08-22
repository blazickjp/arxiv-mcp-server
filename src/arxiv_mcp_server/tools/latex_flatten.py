"""Flatten multi-file LaTeX sources and extract section outlines."""

from __future__ import annotations

from dataclasses import dataclass
import posixpath
import re

from .latex_archive import (
    SourceArchiveLimitError,
    _main_file_score,
    _safe_member_candidate,
)

MAX_FLATTENED_CHARS = 50 * 1024 * 1024
MAX_INCLUDE_DEPTH = 20
MAX_SECTION_COUNT = 10_000
MAX_SECTION_TITLE_CHARS = 200
MAX_MACRO_ROUNDS = 8

_INCLUDE_RE = re.compile(
    r"\\(?P<cmd>subimport|subinputfrom|subincludefrom|import|inputfrom|"
    r"includefrom|input|include)\s*\{(?P<arg1>[^{}]*)\}"
    r"(?:\s*\{(?P<arg2>[^{}]+)\})?"
)
_SECTION_CMD_RE = re.compile(r"\\(section|subsection|subsubsection)\*?\s*\{")
_SECTION_RE = re.compile(
    r"\\(section|subsection|subsubsection)\*?\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
    re.DOTALL,
)
_MACRO_DEF_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?"
    r"\s*(?:{\\([A-Za-z@]+)}|\\([A-Za-z@]+))"
    r"(?:\[(\d+)\])?(?:\[[^{}]*\])?"
)

_INCLUDE_OR_SECTION_RE = re.compile(
    r"\\(?:subimport|subinputfrom|subincludefrom|import|inputfrom|"
    r"includefrom|input|include|section|subsection|subsubsection)\b"
)
_TWO_ARG_IMPORT_KIND = {
    "import": "import",
    "inputfrom": "import",
    "includefrom": "import",
    "subimport": "subimport",
    "subinputfrom": "subimport",
    "subincludefrom": "subimport",
}


@dataclass(frozen=True)
class LatexSection:
    section_id: str
    level: int
    title: str
    start: int
    end: int


def _resolve_include(
    current_file: str,
    requested: str,
    directory: str | None = None,
    *,
    kind: str = "input",
    files: dict[str, str] | None = None,
) -> str | None:
    """Resolve one-arg \\input/\\include or two-arg import-package paths."""
    current_dir = posixpath.dirname(current_file)
    if directory is None:
        return _safe_member_candidate(current_dir, requested)

    if kind == "subimport":
        return _safe_member_candidate(current_dir, directory, requested)

    relative = _safe_member_candidate(current_dir, directory, requested)
    from_root = _safe_member_candidate(directory, requested)
    if files is not None:
        if relative is not None and relative in files:
            return relative
        if from_root is not None and from_root in files:
            return from_root
    return relative or from_root


def _balanced_arg(source: str, open_brace: int) -> tuple[str, int] | None:
    """Return (inner, index_after_close) for a '{...}' starting at open_brace."""
    if open_brace >= len(source) or source[open_brace] != "{":
        return None
    depth = 0
    index = open_brace
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : index], index + 1
        index += 1
    return None


def _skip_ws(source: str, index: int) -> int:
    while index < len(source) and source[index] in " \t\r\n":
        index += 1
    return index


def _plain_section_title(raw: str) -> str:
    """Prefer the text argument of \\texorpdfstring{pdf}{text} when present."""
    title = raw.strip()
    prefix = "\\texorpdfstring"
    if title.startswith(prefix):
        index = _skip_ws(title, len(prefix))
        first = _balanced_arg(title, index)
        if first is not None:
            second = _balanced_arg(title, _skip_ws(title, first[1]))
            if second is not None:
                title = second[0].strip()
    title = re.sub(r"\s+", " ", title).strip()
    return title[:MAX_SECTION_TITLE_CHARS]


def _collect_macros(text: str, masked: str) -> dict[str, tuple[int, str]]:
    macros: dict[str, tuple[int, str]] = {}
    for match in _MACRO_DEF_RE.finditer(masked):
        name = match.group(1) or match.group(2)
        nargs = int(match.group(3) or "0")
        body_start = _skip_ws(masked, match.end())
        extracted = _balanced_arg(text, body_start)
        if extracted is None:
            continue
        macros[name] = (nargs, extracted[0])
    return macros


def _expandable_macros(
    macros: dict[str, tuple[int, str]],
) -> dict[str, tuple[int, str]]:
    """Expand only macros that wrap includes/sections or those macros."""
    expandable = {
        name
        for name, (_nargs, body) in macros.items()
        if _INCLUDE_OR_SECTION_RE.search(body)
    }
    changed = True
    while changed:
        changed = False
        for name, (_nargs, body) in macros.items():
            if name in expandable:
                continue
            if any(
                re.search(rf"\\{re.escape(other)}(?![A-Za-z@])", body)
                for other in expandable
            ):
                expandable.add(name)
                changed = True
    return {name: macros[name] for name in expandable}


def _expand_macros_once(
    text: str, masked: str, macros: dict[str, tuple[int, str]]
) -> tuple[str, bool]:
    if not macros:
        return text, False
    names = sorted(macros, key=len, reverse=True)
    pattern = re.compile(
        r"\\(" + "|".join(re.escape(name) for name in names) + r")(?![A-Za-z@])"
    )
    pieces: list[str] = []
    cursor = 0
    changed = False
    for match in pattern.finditer(masked):
        name = match.group(1)
        nargs, body = macros[name]
        arg_end = match.end()
        args: list[str] = []
        ok = True
        for _ in range(nargs):
            arg_end = _skip_ws(text, arg_end)
            extracted = _balanced_arg(text, arg_end)
            if extracted is None:
                ok = False
                break
            arg, arg_end = extracted
            args.append(arg)
        if not ok:
            continue
        replacement = body
        for index, arg in enumerate(args, start=1):
            replacement = replacement.replace(f"#{index}", arg)
        pieces.append(text[cursor : match.start()])
        pieces.append(replacement)
        cursor = arg_end
        changed = True
    pieces.append(text[cursor:])
    return "".join(pieces), changed


def _expand_macros(text: str, macros: dict[str, tuple[int, str]]) -> str:
    expandable = _expandable_macros(macros)
    if not expandable:
        return text
    for _ in range(MAX_MACRO_ROUNDS):
        masked = _mask_tex_comments(text)
        text, changed = _expand_macros_once(text, masked, expandable)
        if not changed:
            break
        if len(text) > MAX_FLATTENED_CHARS:
            raise SourceArchiveLimitError("flattened LaTeX source exceeds safety limit")
    return text


def _mask_macro_definitions(masked: str) -> str:
    """Blank newcommand bodies so definition-time includes are not followed."""
    chars = list(masked)
    for match in _MACRO_DEF_RE.finditer(masked):
        body_start = _skip_ws(masked, match.end())
        extracted = _balanced_arg(masked, body_start)
        if extracted is None:
            continue
        _body, end = extracted
        for index in range(body_start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def _mask_tex_comments(source: str) -> str:
    """Replace TeX comments with spaces while preserving offsets and newlines."""
    masked: list[str] = []
    index = 0
    while index < len(source):
        char = source[index]
        if char == "%":
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and source[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                while index < len(source) and source[index] not in "\r\n":
                    masked.append(" ")
                    index += 1
                continue
        masked.append(char)
        index += 1
    return "".join(masked)


def _flatten_source(files: dict[str, str]) -> tuple[str, str]:
    """Select the main document and inline local includes within a hard budget."""
    content, main_file, _unmatched = _flatten_source_with_unmatched(files)
    return content, main_file


def _flatten_source_with_unmatched(
    files: dict[str, str],
) -> tuple[str, str, tuple[str, ...]]:
    """Flatten includes/imports and report commands that did not resolve."""
    main_file = max(files, key=lambda name: _main_file_score(name, files[name]))
    output: list[str] = []
    output_chars = 0
    unmatched: list[str] = []
    inherited: dict[str, tuple[int, str]] = {}

    def emit(value: str) -> None:
        nonlocal output_chars
        output_chars += len(value)
        if output_chars > MAX_FLATTENED_CHARS:
            raise SourceArchiveLimitError("flattened LaTeX source exceeds safety limit")
        output.append(value)

    def expand(
        name: str,
        stack: tuple[str, ...],
        depth: int,
        macros: dict[str, tuple[int, str]],
    ) -> None:
        text = files.get(name, "")
        if depth >= MAX_INCLUDE_DEPTH:
            emit(text)
            return
        masked = _mask_tex_comments(text)
        macros = dict(macros)
        macros.update(_collect_macros(text, masked))
        text = _expand_macros(text, macros)
        masked = _mask_macro_definitions(_mask_tex_comments(text))
        cursor = 0
        for match in _INCLUDE_RE.finditer(masked):
            emit(text[cursor : match.start()])
            command = match.group("cmd")
            arg1 = match.group("arg1")
            arg2 = match.group("arg2")
            kind = _TWO_ARG_IMPORT_KIND.get(command)
            if kind is None:
                target = _resolve_include(name, arg1, files=files)
            elif arg2 is None:
                target = None
            else:
                target = _resolve_include(name, arg2, arg1, kind=kind, files=files)
            if target is not None and target in files and target not in stack:
                expand(target, (*stack, target), depth + 1, macros)
            else:
                snippet = re.sub(r"\s+", " ", match.group(0)).strip()
                if snippet and snippet not in unmatched:
                    unmatched.append(snippet)
            cursor = match.end()
        emit(text[cursor:])

    expand(main_file, (main_file,), 0, inherited)
    return "".join(output), main_file, tuple(unmatched)


def _parse_sections(source: str) -> list[LatexSection]:
    raw: list[tuple[int, str, str, int]] = []
    levels = {"section": 1, "subsection": 2, "subsubsection": 3}
    counters = [0, 0, 0]
    masked = _mask_macro_definitions(_mask_tex_comments(source))
    for match in _SECTION_CMD_RE.finditer(masked):
        if len(raw) >= MAX_SECTION_COUNT:
            raise SourceArchiveLimitError("LaTeX source contains too many sections")
        extracted = _balanced_arg(source, match.end() - 1)
        if extracted is None:
            fallback = _SECTION_RE.match(masked[match.start() :])
            if fallback is None:
                continue
            title = re.sub(r"\s+", " ", fallback.group(2)).strip()
            title = title[:MAX_SECTION_TITLE_CHARS]
        else:
            title = _plain_section_title(extracted[0])
        if not title:
            continue
        level = levels[match.group(1)]
        counters[level - 1] += 1
        for index in range(level, 3):
            counters[index] = 0
        section_id = ".".join(str(value) for value in counters[:level])
        raw.append((level, section_id, title, match.start()))

    sections: list[LatexSection] = []
    for index, (level, section_id, title, start) in enumerate(raw):
        end = len(source)
        for next_level, _next_id, _next_title, next_start in raw[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        sections.append(LatexSection(section_id, level, title, start, end))
    return sections


def _extract_section(
    source: str, sections: list[LatexSection], section_id: str
) -> str | None:
    needle = section_id.strip().casefold()
    for section in sections:
        if (
            section.section_id.casefold() == needle
            or section.title.casefold() == needle
        ):
            return source[section.start : section.end].rstrip()
    return None
