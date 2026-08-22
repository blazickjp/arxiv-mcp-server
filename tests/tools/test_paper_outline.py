"""Tests for markdown outline, section, and passage retrieval tools."""

from __future__ import annotations

import json

import pytest

from arxiv_mcp_server.tools import paper_outline as outline_module
from arxiv_mcp_server.tools.paper_outline import (
    handle_get_paper_outline,
    handle_read_paper_section,
    handle_search_paper_text,
    parse_markdown_sections,
)

SAMPLE_PAPER = """# Introduction

This is the intro with an equation $E = mc^2$.

## Background

Background text.

### Related Work

Prior art.

## Methods

Methods body with a table:

| a | b |
|---|---|
| 1 | 2 |

$$
\\int_0^1 x dx
$$

# Results

Results section.

## Methods

Duplicate title under Results.

# Conclusion

Final words.
"""


@pytest.fixture
def patch_storage(temp_storage_path, monkeypatch):
    monkeypatch.setattr(
        outline_module.settings,
        "_get_storage_path_from_args",
        lambda: temp_storage_path,
    )
    return temp_storage_path


def _write_paper(storage, paper_id: str, content: str, version: str | None = "v1"):
    (storage / f"{paper_id}.md").write_text(content, encoding="utf-8")
    if version:
        (storage / f"{paper_id}.meta.json").write_text(
            json.dumps({"arxiv_version": version}), encoding="utf-8"
        )


def test_parse_handles_duplicate_and_missing_levels():
    sections = parse_markdown_sections(SAMPLE_PAPER)
    ids = [s.section_id for s in sections]
    assert ids[0] == "1"
    # ## under # Introduction
    assert "1.1" in ids and "1.2" in ids
    # ### Related Work under Background -> 1.1.1
    assert "1.1.1" in ids
    # Second top-level Results -> 2
    assert "2" in ids
    # Duplicate "Methods" titles have distinct IDs
    methods = [s for s in sections if s.title == "Methods"]
    assert len(methods) == 2
    assert methods[0].section_id != methods[1].section_id


def test_parse_malformed_and_fence_ignored():
    md = """# Real

```
# Not A Heading
```

## Also Real

#   Weird Spaces  

#
Empty title ignored

### Skipped Level
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]
    assert "Not A Heading" not in titles
    assert "Real" in titles
    assert "Also Real" in titles
    assert "Weird Spaces" in titles
    # h3 after h2 -> 1.1.1 style with missing? Actually after ## (1.1), ### is 1.1.1
    skipped = [s for s in sections if s.title == "Skipped Level"][0]
    assert skipped.level == 3


def test_parse_no_headings_synthetic():
    sections = parse_markdown_sections("just plain text\nand more")
    assert len(sections) == 1
    assert sections[0].section_id == "1"
    assert sections[0].title == "(document)"
    assert sections[0].end == len("just plain text\nand more")


def test_section_does_not_cross_siblings():
    sections = parse_markdown_sections(SAMPLE_PAPER)
    intro = next(s for s in sections if s.section_id == "1")
    body = SAMPLE_PAPER[intro.start : intro.end]
    assert "# Results" not in body
    assert "Final words" not in body
    methods = next(s for s in sections if s.section_id == "1.2")
    methods_body = SAMPLE_PAPER[methods.start : methods.end]
    assert "Results section" not in methods_body
    assert "Duplicate title" not in methods_body
    assert "table" in methods_body
    assert "\\int_0^1" in methods_body


@pytest.mark.asyncio
async def test_outline_and_version_fields(patch_storage):
    _write_paper(patch_storage, "2505.13525", SAMPLE_PAPER, version="v3")
    response = await handle_get_paper_outline({"paper_id": "2505.13525"})
    result = json.loads(response[0].text)
    assert result["status"] == "success"
    assert result["paper_id"] == "2505.13525"
    assert result["arxiv_version"] == "v3"
    assert result["versioned_id"] == "2505.13525v3"
    assert result["total_sections"] >= 6
    assert result["sections"][0]["id"] == "1"


@pytest.mark.asyncio
async def test_outline_pagination(patch_storage):
    _write_paper(patch_storage, "2505.13525", SAMPLE_PAPER)
    response = await handle_get_paper_outline(
        {"paper_id": "2505.13525", "start": 0, "max_sections": 2}
    )
    result = json.loads(response[0].text)
    assert result["returned_sections"] == 2
    assert result["is_truncated"] is True
    assert result["next_start"] == 2


@pytest.mark.asyncio
async def test_read_section_bounds_and_invalid(patch_storage):
    big = "# Huge\n" + ("x" * 30_000)
    _write_paper(patch_storage, "2505.13525", big)
    response = await handle_read_paper_section(
        {"paper_id": "2505.13525", "section_id": "1", "max_chars": 100}
    )
    result = json.loads(response[0].text)
    assert result["status"] == "success"
    assert result["is_truncated"] is True
    assert result["returned_chars"] == 100
    assert "UNTRUSTED EXTERNAL CONTENT" in result["content"]
    assert result["next_start"] == 100

    bad = await handle_read_paper_section(
        {"paper_id": "2505.13525", "section_id": "99.99"}
    )
    err = json.loads(bad[0].text)
    assert err["status"] == "error"
    assert "not found" in err["message"].lower()


@pytest.mark.asyncio
async def test_search_passages_and_empty(patch_storage):
    _write_paper(patch_storage, "2505.13525", SAMPLE_PAPER)
    response = await handle_search_paper_text(
        {
            "paper_id": "2505.13525",
            "query": "table",
            "max_passages": 3,
            "passage_chars": 200,
        }
    )
    result = json.loads(response[0].text)
    assert result["status"] == "success"
    assert result["returned_passages"] >= 1
    passage = result["passages"][0]
    assert "start" in passage and "end" in passage
    assert "match_start" in passage and "match_end" in passage
    assert passage["section_id"] is not None
    assert "UNTRUSTED EXTERNAL CONTENT" in passage["excerpt"]

    empty = await handle_search_paper_text({"paper_id": "2505.13525", "query": ""})
    empty_result = json.loads(empty[0].text)
    assert empty_result["status"] == "success"
    assert empty_result["returned_passages"] == 0

    miss = await handle_search_paper_text(
        {"paper_id": "2505.13525", "query": "zzznomatchzzz"}
    )
    miss_result = json.loads(miss[0].text)
    assert miss_result["returned_passages"] == 0


@pytest.mark.asyncio
async def test_not_found_and_no_heading_paper(patch_storage):
    missing = await handle_get_paper_outline({"paper_id": "9999.99999"})
    assert json.loads(missing[0].text)["status"] == "error"

    _write_paper(patch_storage, "2505.13525", "no headings here")
    outline = json.loads(
        (await handle_get_paper_outline({"paper_id": "2505.13525"}))[0].text
    )
    assert outline["total_sections"] == 1
    assert outline["sections"][0]["id"] == "1"
    section = json.loads(
        (
            await handle_read_paper_section(
                {"paper_id": "2505.13525", "section_id": "1"}
            )
        )[0].text
    )
    assert section["status"] == "success"
    assert "no headings here" in section["content"]
