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
    search_passages,
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
    assert "UNTRUSTED EXTERNAL CONTENT" in result["content_warning"]
    assert "UNTRUSTED" not in result["content"]
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
    assert "UNTRUSTED EXTERNAL CONTENT" not in passage["excerpt"]
    assert "table" in passage["excerpt"].casefold()

    empty = await handle_search_paper_text({"paper_id": "2505.13525", "query": ""})
    empty_result = json.loads(empty[0].text)
    assert empty_result["status"] == "success"
    assert empty_result["returned_passages"] == 0

    miss = await handle_search_paper_text(
        {"paper_id": "2505.13525", "query": "zzznomatchzzz"}
    )
    miss_result = json.loads(miss[0].text)
    assert miss_result["returned_passages"] == 0


def _overlap_ratio(a: dict, b: dict) -> float:
    overlap = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    if overlap == 0:
        return 0.0
    shorter = min(a["end"] - a["start"], b["end"] - b["start"])
    return overlap / shorter if shorter else 0.0


def test_search_passages_dedupes_overlapping_windows():
    """Nearby repeats of the same term must not flood max_passages."""
    # Cluster "routing" hits ~100 chars apart in one section (repro pattern).
    filler = "x" * 80
    abstract = (
        f"Abstract discusses routing {filler} then routing again {filler} "
        f"with more routing {filler} and routing once more {filler} "
        f"final routing mention."
    )
    methods = "Methods use routing for expert selection in MoE layers."
    results = "Results show routing improves latency under load."
    conclusion = "Conclusion: routing is the key bottleneck."
    related = "Related work compares routing heuristics."
    md = (
        f"# Abstract\n\n{abstract}\n\n"
        f"# Methods\n\n{methods}\n\n"
        f"# Results\n\n{results}\n\n"
        f"# Related Work\n\n{related}\n\n"
        f"# Conclusion\n\n{conclusion}\n"
    )
    sections = parse_markdown_sections(md)
    passages = search_passages(
        md, sections, "routing", max_passages=5, passage_chars=400
    )
    assert 1 <= len(passages) <= 5
    # Pairwise windows must not be near-duplicates.
    for i, a in enumerate(passages):
        for b in passages[i + 1 :]:
            assert _overlap_ratio(a, b) <= 0.5, (a["start"], b["start"])
    # Prefer distinct sections over five abstract near-duplicates.
    section_ids = {p["section_id"] for p in passages if p["section_id"]}
    assert len(section_ids) >= min(4, len(passages))


def test_search_passages_respects_max_passages_after_dedupe():
    md = "\n\n".join(
        f"# Section {i}\n\nUnique routing topic {i} discussion." for i in range(1, 12)
    )
    sections = parse_markdown_sections(md)
    passages = search_passages(
        md, sections, "routing", max_passages=3, passage_chars=120
    )
    assert len(passages) == 3
    assert len({p["section_id"] for p in passages}) == 3


@pytest.mark.asyncio
async def test_search_paper_text_dedupes_via_handler(patch_storage):
    filler = "y" * 90
    body = (
        f"# Abstract\n\nrouting {filler} routing {filler} routing\n\n"
        f"# Methods\n\nWe study routing strategies.\n\n"
        f"# Results\n\nrouting wins on latency.\n"
    )
    _write_paper(patch_storage, "2410.17954", body)
    response = await handle_search_paper_text(
        {
            "paper_id": "2410.17954",
            "query": "routing",
            "max_passages": 5,
            "passage_chars": 300,
        }
    )
    result = json.loads(response[0].text)
    assert result["status"] == "success"
    assert result["returned_passages"] == len(result["passages"]) <= 5
    passages = result["passages"]
    assert len(passages) >= 2
    for i, a in enumerate(passages):
        for b in passages[i + 1 :]:
            assert _overlap_ratio(a, b) <= 0.5
    assert len({p["section_id"] for p in passages}) >= 2


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


BARE_HTML_PAPER = """Attention Is All You Need

Abstract

The dominant sequence transduction models are based on complex recurrent.

Introduction

Recurrent neural networks, long short-term memory and gated recurrent.

Background

The goal of reducing sequential computation forms the foundation.

Related Work

The Transformer is the first transduction model relying entirely.

Methods

We propose a new architecture.

Experiments

This section describes our experimental setup.

Results

On the WMT 2014 English-to-German translation task.

Discussion

In this work we presented the Transformer.

Conclusion

We are excited about the future of attention-based models.

References

[1] Someone et al.
"""


NUMBERED_PAPER = """1 Introduction

Intro body about transformers.

2 Background

Background body.

3 Model Architecture

Top-level model section.

3.1 Attention

Scaled dot-product attention.

3.2 Multi-Head Attention

Multi-head details.

4 Experiments

Experiment body.

4.1 Training

Training details.
"""


def test_parse_bare_arxiv_html_titles():
    sections = parse_markdown_sections(BARE_HTML_PAPER)
    titles = [s.title for s in sections]
    assert "(document)" not in titles
    for expected in (
        "Abstract",
        "Introduction",
        "Background",
        "Related Work",
        "Methods",
        "Experiments",
        "Results",
        "Discussion",
        "Conclusion",
        "References",
    ):
        assert expected in titles, f"missing {expected}"
    # Paper title line should not become a section.
    assert "Attention Is All You Need" not in titles
    intro = next(s for s in sections if s.title == "Introduction")
    body = BARE_HTML_PAPER[intro.start : intro.end]
    assert "Recurrent neural networks" in body
    assert body.lstrip().startswith("Introduction")
    # Sibling boundary: Results text should not leak into Introduction.
    assert "WMT 2014" not in body
    bg = next(s for s in sections if s.title == "Background")
    assert BARE_HTML_PAPER[bg.start : bg.end].lstrip().startswith("Background")


def test_parse_numbered_headings():
    sections = parse_markdown_sections(NUMBERED_PAPER)
    by_title = {s.title: s for s in sections}
    assert by_title["Introduction"].section_id == "1"
    assert by_title["Introduction"].level == 1
    assert by_title["Background"].section_id == "2"
    assert by_title["Model Architecture"].section_id == "3"
    assert by_title["Attention"].section_id == "3.1"
    assert by_title["Attention"].level == 2
    assert by_title["Multi-Head Attention"].section_id == "3.2"
    assert by_title["Experiments"].section_id == "4"
    assert by_title["Training"].section_id == "4.1"
    attention = by_title["Attention"]
    body = NUMBERED_PAPER[attention.start : attention.end]
    assert "Scaled dot-product" in body
    assert "Multi-head details" not in body


def test_parse_atx_still_preferred_over_bare():
    md = """# Introduction

Prose mentioning Background in a sentence should not split.

Background

Real bare section after ATX.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]
    assert titles[0] == "Introduction"
    assert "Background" in titles
    assert sections[0].level == 1


@pytest.mark.asyncio
async def test_bare_title_read_section_and_search_clean(patch_storage):
    _write_paper(patch_storage, "1706.03762", BARE_HTML_PAPER)
    outline = json.loads(
        (await handle_get_paper_outline({"paper_id": "1706.03762"}))[0].text
    )
    assert outline["status"] == "success"
    assert outline["total_sections"] >= 8
    titles = [s["title"] for s in outline["sections"]]
    assert "Introduction" in titles
    assert titles != ["(document)"]

    section = json.loads(
        (
            await handle_read_paper_section(
                {"paper_id": "1706.03762", "section_id": "Introduction"}
            )
        )[0].text
    )
    assert section["status"] == "success"
    assert "Recurrent neural networks" in section["content"]

    search = json.loads(
        (
            await handle_search_paper_text(
                {
                    "paper_id": "1706.03762",
                    "query": "transduction",
                    "passage_chars": 200,
                }
            )
        )[0].text
    )
    assert search["returned_passages"] >= 1
    excerpt = search["passages"][0]["excerpt"]
    assert "UNTRUSTED EXTERNAL CONTENT" not in excerpt
    assert "transduction" in excerpt.casefold()


EXPERTFLOW_HTML_STYLE = """ExpertFlow title line

Abstract.

The abstract body.

1.
Introduction

Intro body.

2.
Related Work

2.1.
Mixture-of-Experts (MoE)

MoE body.

3.
Method

3.1.
System Design Overview

Overview body.

3.2.
Routing Path Predictor (RPP)

Predictor body.

4.
Evaluation

Eval body.

5.
Conclusion

Conclusion body.

Acknowledgements.

This research is supported.

References

Aminabadi
et al.
(2022)
DeepSpeed paper.
In
2023 USENIX Annual Technical Conference (USENIX ATC 23)
,
Boston, MA
,
2021 USENIX Annual Technical Conference (USENIX ATC 21)
,
pp. 551–564
.
Appendix
Should not appear after references terminator.
"""


def test_outline_stops_at_references_and_keeps_method_subsections():
    """Regression for #229: ExpertFlow-style HTML→text outlines.

    Split ``3.`` / ``Method`` / ``3.1.`` lines must yield nested subsections,
    and bibliography venue lines must not become outline sections.
    """
    sections = parse_markdown_sections(EXPERTFLOW_HTML_STYLE)
    titles = [s.title for s in sections]
    assert "Method" in titles
    assert "System Design Overview" in titles
    assert "Routing Path Predictor (RPP)" in titles
    assert "References" in titles
    assert titles[-1] == "References"
    assert not any("USENIX" in t for t in titles)
    assert "Appendix" not in titles

    method = next(s for s in sections if s.title == "Method")
    assert method.level == 1
    children = [s for s in sections if s.section_id.startswith(method.section_id + ".")]
    child_titles = [s.title for s in children]
    assert "System Design Overview" in child_titles
    assert "Routing Path Predictor (RPP)" in child_titles
    assert any(s.section_id == f"{method.section_id}.1" for s in children)
    assert any(s.section_id == f"{method.section_id}.2" for s in children)


def test_reject_year_prefixed_venue_and_loose_bare_titles():
    """Tighten numbered/bare heuristics so citation lines are not headings."""
    md = """1 Introduction

Body.

2023 USENIX Annual Technical Conference (USENIX ATC 23)

Still body before real terminator.

References

More venue noise.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]
    assert titles == ["Introduction", "References"]
    assert not any("USENIX" in t for t in titles)

    # Extra tokens must not match bare known titles.
    noisy = parse_markdown_sections("Method discussion continues here\n\nMore.\n")
    assert noisy[0].title == "(document)"


def test_bibliography_terminator_alias():
    sections = parse_markdown_sections(
        "Introduction\n\nIntro.\n\nBibliography\n\n1 Some Paper Title Here\n"
    )
    titles = [s.title for s in sections]
    assert titles[-1] == "Bibliography"
    assert "Some Paper Title Here" not in titles
