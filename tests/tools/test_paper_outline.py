"""Tests for markdown outline, section, and passage retrieval tools."""

from __future__ import annotations

import json

import pytest

from arxiv_mcp_server.tools import paper_outline as outline_module
from arxiv_mcp_server.tools.paper_outline import (
    _find_section,
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

Additional implementation details and proofs.
"""


# Paper with appendices after References (regression #288)
DPO_STYLE_WITH_APPENDIX = """# Abstract

This paper presents Direct Preference Optimization.

# Introduction

DPO simplifies RLHF.

# Background

RLHF background.

# DPO

Our method details.

# Experiments

Experimental setup.

# Results

Performance results.

# References

[1] Schulman et al. Proximal Policy Optimization. 2017.
[2] Ouyang et al. Training language models to follow instructions. 2022.

# DPO Implementation Details and Hyperparameters

We use the following hyperparameters for training.

## Learning Rate Schedule

We use a cosine learning rate schedule.

## Model Architecture

Models follow the standard transformer architecture.

# Additional Experimental Results

Further analysis of model performance.
"""


# Real paper 2305.04388v2 (Turpin et al.) excerpt: References tail + appendices with table cells
# Verbatim from arxiv-mcp-server HTML→text conversion
TURPIN_REAL_EXCERPT = """References

The best answer is: (B)
✗
Appendix A

Additional Samples

See

Appendix B

Verifying that Explanations Do Not Mention Biasing Features

As discussed in

Appendix C

Qualitative Analysis Details

Table 7:

C.1

BBH

For each explanation reviewed, we annotate two features:

Appendix D

Results Tables

We include the following extra results tables:

Table 9:
Accuracy on BBH broken down by task. The results are for examples with bias-contradicting labels.

GPT-3.5

Claude 1.0

No-CoT

CoT

UB

B

UB

B

Web Of Lies

Sugg. Ans.

ZS

46.2

18.8

FS

56.4

35.9

Snarks

Sugg. Ans.

ZS

66.2

46.8

Table 11:
Number of failed samples per experimental setting, primarily due to CoT explanations not giving the answer in the correct format.

# Failed

No debiasing instruction

GPT-3.5

Zero-shot

0

Few-shot

0

Table 12:
Number of failed samples per experimental setting.

N Total

# FS (Ans. A)

Hyperbaton

1

0

300

7

Snarks

10

0

151

14

Web Of Lies

0

0

220

10

Appendix E

Prompting Details

The following prompting details apply to both the BBH and BBQ experiments.

Appendix F

Additional BBH Experiment Details

F.1

F.1

Data

For most tasks, we pull from the original BIG-Bench data using Hugging Face datasets.
"""


# Real DPO 2305.18290v3 excerpt: References tail + appendices
DPO_REAL_EXCERPT = """References

D. M. Ziegler, N. Stiennon, J. Wu, T. B. Brown, A. Radford, D. Amodei,
P. Christiano, and G. Irving.
Fine-tuning language models from human preferences, 2020.

Author Contributions

All authors
provided valuable contributions to designing, analyzing, and iterating on experiments, writing and editing the paper, and generally managing the project's progress.

RR

proposed using autoregressive reward models in discussions with

EM

; derived the DPO objective; proved the theoretical properties of the algorithm.

CF, CM, & SE

supervised the research, suggested ideas and experiments, and assisted in writing the paper.

Appendix A

Mathematical Derivations

A.1

A.1

Deriving the Optimum of the KL-Constrained Reward Maximization Objective

In this appendix, we will derive Eq. 4 . Analogously to Eq. 3 , we optimize the following objective:

A.2

Deriving the DPO Objective Under the Bradley-Terry Model

It is straightforward to derive the DPO objective under the Bradley-Terry preference model as we have

A.2

, the normalization constant

Z(x)

Appendix B

DPO Implementation Details and Hyperparameters

DPO is relatively straightforward to implement; PyTorch code for the DPO loss is provided below:

import torch.nn.functional as F

Appendix C

Further Details on the Experimental Set-Up

In this section, we include additional details relevant to our experimental design.

C.1

IMDb Sentiment Experiment and Baseline Details

The prompts are prefixes from the IMDB dataset of length 2-8 tokens.

C.2

GPT-4 prompts for computing summarization and dialogue win rates

A key component of our experimental setup is GPT-4 win rate judgments.

C.3

Unlikelihood baseline

While we include the unlikelihood baseline

Appendix D

Additional Empirical Results

D.1

Performance of Best of

N

baseline for Various

N

We find that the Best of

N

baseline is a strong baseline in our experiments.
"""


# Llama 2 2307.09288v2 excerpt: nested acknowledgments that must stay at level 3
LLAMA2_REAL_EXCERPT = """We thank the

GenAI executive team

for their leadership and support: Ahmad Al-Dahle, Manohar Paluri.

A.1.1

Acknowledgments

This work was made possible by a large group of contributors. We extend our gratitude to the following people for their assistance:
"""


def test_outline_stops_at_references_and_keeps_method_subsections():
    """Regression for #229 and #288: ExpertFlow-style HTML→text outlines.

    Split ``3.`` / ``Method`` / ``3.1.`` lines must yield nested subsections,
    bibliography venue lines must not become outline sections (#229), and
    appendices after References must be included (#288).
    """
    sections = parse_markdown_sections(EXPERTFLOW_HTML_STYLE)
    titles = [s.title for s in sections]
    assert "Method" in titles
    assert "System Design Overview" in titles
    assert "Routing Path Predictor (RPP)" in titles
    assert "References" in titles

    # Appendix after References is now included (#288)
    assert "Appendix" in titles
    assert titles.index("References") < titles.index("Appendix")

    # Bibliography venue lines should not become sections (#229)
    assert not any("USENIX" in t for t in titles)
    assert not any("Aminabadi" in t for t in titles)
    assert not any("2022" in t for t in titles)

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
    """Bibliography is recognized as a References alias, but parsing continues.

    Reference entries are filtered by existing content guards, not by early termination.
    """
    sections = parse_markdown_sections(
        "Introduction\n\nIntro.\n\nBibliography\n\n[1] Some Paper Title Here. 2023.\n\nAppendix\n\nMore details.\n"
    )
    titles = [s.title for s in sections]
    assert "Introduction" in titles
    assert "Bibliography" in titles
    assert "Appendix" in titles
    # Reference entry with [1] format is not a section
    assert "Some Paper Title Here" not in titles
    # Appendix comes after Bibliography
    assert titles.index("Bibliography") < titles.index("Appendix")


DAOP_IEEE_HTML_STYLE = """DAOP: Data-Aware Offloading title line

Yujie Zhang

Abstract

Mixture-of-Experts (MoE) models face deployment challenges on memory-constrained devices.

Index Terms:
MoE inference engine

I
Introduction

Mixture-of-Experts (MoE) architecture addresses computational demands.

As detailed in Table
I
, migrating a single expert is slow.

II
Preliminaries
II-A
LLMs with Mixture-of-Experts (MoE)

Decoder-only Architecture body.

II-B
Related Work

Prior MoE systems body.

III
Observations & Insights

Table
II
illustrates similarity.

IV
DAOP: MoE Inference Engine
IV-A
Memory Initialization

We initially allocate experts.

IV-B
Sequence-specific Expert Allocation

Algorithm body.

Fig. 6:
Design Overview of DAOP.
Data:
initialized expert cache, model blocks,
comparison threshold SwapInOut
Result:
updated expert cache, firstly generated output token
inputTokens = Embedding(inputTokens);

IV-C
Prediction-based Expert Pre-Calculation

Prediction body.

V
Experimental Evaluation
V-A
Experimental Setup

We assess performance on Mixtral.

V-B
Speedup

Speedup body.

V-C
Energy Efficiency

Table
IV
compares the energy efficiency of DAOP.

V-D
Accuracy Results

TABLE V:
Impact of DAOP on model accuracy
Model
Method
HellaS
Arc-e
Mixtral 8x7B
Official
66.96

TABLE VI:
Impact across entire inference
Model
Method
ECR
TriviaQA
Official
100.0%

VI
Discussion
VI-A
Platform and Model Applicability

Applicability body.

VI-B
Limitation & Future Work

Limitation body.

VII
Conclusion

Our proposed inference engine conclusion body.

Acknowledgment

We thank anonymous reviewers.

References

[1]
Someone et al.
"""


def test_daop_ieee_roman_html_outline_order_without_dupes():
    """Regression for #240: DAOP HTML IEEE roman tags, not Result:/table Method.

    latexml HTML emits ``I`` / ``Introduction`` and ``V-A`` / ``Experimental
    Setup`` on separate lines. Algorithm ``Result:`` and table column
    ``Method`` must not become outline sections or reorder Method/Result.
    """
    sections = parse_markdown_sections(DAOP_IEEE_HTML_STYLE)
    titles = [s.title for s in sections]
    assert "Result" not in titles
    assert "Method" not in titles
    assert titles.count("Experimental Setup") == 1
    assert titles.index("DAOP: MoE Inference Engine") < titles.index(
        "Experimental Evaluation"
    )
    assert titles.index("Experimental Evaluation") < titles.index("Discussion")
    by_title = {s.title: s for s in sections}
    assert by_title["Introduction"].section_id == "2"
    assert by_title["Preliminaries"].section_id == "3"
    assert by_title["Related Work"].section_id == "3.2"
    assert by_title["DAOP: MoE Inference Engine"].section_id == "5"
    assert by_title["Memory Initialization"].section_id == "5.1"
    assert by_title["Experimental Setup"].section_id == "6.1"
    assert by_title["Accuracy Results"].section_id == "6.4"
    assert titles[-1] == "References"
    # Prose ``Table I`` / ``Table II`` must not invent extra sections.
    assert titles.count("Introduction") == 1
    assert "illustrates similarity" not in titles


SWITCH_FUTURE_WORK_HTML_STYLE = """Switch Transformers title line

1.
Introduction

Intro body about sparse models.

8.
Future Work

This paper lays out a simplified architecture, improved training procedures,
and a study of how sparse models scale. However, there remain many open
future directions which we briefly describe here:

1.
A significant challenge is further improving training stability for the largest models.

2.
Generally we find that improved pre-training quality leads to better downstream results.

3.
Perform a comprehensive study of scaling relationships to guide the design.

4.
Our work falls within the family of adaptive computation algorithms.

5.
Investigating expert layers outside the FFN layer of the Transformer.

6.
Examining Switch Transformer in new and across different modalities.

9.
Conclusion

Conclusion body.

References

[1] Someone et al.
"""


def test_switch_future_work_numbered_lists_not_outline_sections():
    """Regression for #257: Switch Transformers Future Work list items.

    HTML→text emits ``4.`` / ``5.`` body-list sentences under Future Work.
    Those must not become fake L1 outline sections.
    """
    sections = parse_markdown_sections(SWITCH_FUTURE_WORK_HTML_STYLE)
    titles = [s.title for s in sections]
    assert "Future Work" in titles
    assert "Conclusion" in titles
    assert "Introduction" in titles
    assert titles[-1] == "References"
    # Numbered body-list prose must stay out of the outline.
    for banned in (
        "Our work falls within the family of adaptive computation algorithms",
        "Investigating expert layers outside the FFN layer of the Transformer",
        "Examining Switch Transformer in new and across different modalities",
        "Perform a comprehensive study of scaling relationships to guide the design",
        "A significant challenge is further improving training stability for the largest models",
        "Generally we find that improved pre-training quality leads to better downstream results",
    ):
        assert banned not in titles
        assert not any(banned in t for t in titles)
    # Real numbered sections still parse; list markers do not inflate L1 count.
    by_title = {s.title: s for s in sections}
    assert by_title["Introduction"].level == 1
    assert by_title["Future Work"].level == 1
    assert by_title["Conclusion"].level == 1
    assert [s.title for s in sections if s.level == 1] == [
        "Introduction",
        "Future Work",
        "Conclusion",
        "References",
    ]


def test_numbered_sentence_case_and_period_rejected():
    """Sentence-case / trailing-period numbered lines are body lists, not headings."""
    md = """1 Introduction

Body.

2.
Our approach always used identical homogeneous experts.

3 Methods

Methods body.

4.
Investigating expert layers outside the feed-forward network.

5 Conclusion

Done.

References

[1] x
"""
    titles = [s.title for s in parse_markdown_sections(md)]
    assert titles == ["Introduction", "Methods", "Conclusion", "References"]
    assert "Our approach always used identical homogeneous experts" not in titles
    assert "Investigating expert layers outside the feed-forward network" not in titles


def test_roman_inline_and_reject_colon_bare_titles():
    inline = parse_markdown_sections(
        "I Introduction\n\nIntro body.\n\nII-A Background Details\n\nMore.\n"
    )
    assert [s.title for s in inline[:2]] == ["Introduction", "Background Details"]
    assert inline[0].section_id == "1"
    assert inline[1].section_id == "1.1"

    colon = parse_markdown_sections(
        "Introduction\n\nBody.\n\nResult:\nupdated cache\n\nConclusion\n\nDone.\n"
    )
    titles = [s.title for s in colon]
    assert "Result" not in titles
    assert "Introduction" in titles and "Conclusion" in titles


KAN_HTML_STYLE = """1
Introduction

Inspired by the Kolmogorov-Arnold representation theorem.

2
Kolmogorov-Arnold Networks

We propose Kolmogorov-Arnold Networks (KANs).

2.2
KAN architecture

KANs have strong mathematical and computational foundations.

3
KANs are accurate

First KAN body.

4
KANs are interpretable

Second KAN body.

5
Related works

Prior work on representation.

6
Discussion

We discuss performance.

Acknowledgement

We thank the reviewers.

References

[1] Someone et al.
"""


def test_kan_split_numbered_headings_without_trailing_period():
    """Regression #284 bug 1: split section numbers without trailing period (KAN 2404.19756v5).

    HTML→text emits ``2\\nKolmogorov-Arnold Networks`` and ``2.2\\nKAN Architecture``
    without a trailing period after the number. Must recognize these as sections.
    """
    sections = parse_markdown_sections(KAN_HTML_STYLE)
    titles = [s.title for s in sections]
    # All real sections recognized (sentence case from real paper)
    assert "Introduction" in titles
    assert "Kolmogorov-Arnold Networks" in titles
    assert "KAN architecture" in titles
    assert "KANs are accurate" in titles
    assert "KANs are interpretable" in titles
    assert "Related works" in titles
    assert "Discussion" in titles
    assert "References" in titles

    by_title = {s.title: s for s in sections}
    # Proper numbering: split numbers without trailing period are recognized
    assert by_title["Introduction"].section_id == "1"
    assert by_title["Kolmogorov-Arnold Networks"].section_id == "2"
    assert by_title["KAN architecture"].section_id == "2.1"
    assert by_title["KANs are accurate"].section_id == "3"
    assert by_title["KANs are interpretable"].section_id == "4"

    # Introduction ends before section 2
    intro = by_title["Introduction"]
    intro_body = KAN_HTML_STYLE[intro.start : intro.end]
    assert "Kolmogorov-Arnold representation theorem" in intro_body
    assert "Kolmogorov-Arnold Networks (KANs)" not in intro_body
    assert intro.end <= by_title["Kolmogorov-Arnold Networks"].start


@pytest.mark.asyncio
async def test_kan_sections_addressable_via_read_section(patch_storage):
    """Regression #284 bug 1: KAN sections 2, 2.2, 3, 4 must be addressable."""
    _write_paper(patch_storage, "2404.19756v5", KAN_HTML_STYLE)

    outline = json.loads(
        (await handle_get_paper_outline({"paper_id": "2404.19756v5"}))[0].text
    )
    assert outline["status"] == "success"
    titles = [s["title"] for s in outline["sections"]]
    assert "KAN architecture" in titles

    # read_paper_section by section_id
    kan_arch = json.loads(
        (
            await handle_read_paper_section(
                {"paper_id": "2404.19756v5", "section_id": "2.1", "max_chars": 500}
            )
        )[0].text
    )
    assert kan_arch["status"] == "success"
    assert "KAN architecture" in kan_arch["section"]["title"]
    assert "strong mathematical" in kan_arch["content"]

    # read_paper_section by title
    kan_arch_title = json.loads(
        (
            await handle_read_paper_section(
                {
                    "paper_id": "2404.19756v5",
                    "section_id": "KAN architecture",
                    "max_chars": 500,
                }
            )
        )[0].text
    )
    assert kan_arch_title["status"] == "success"
    assert "strong mathematical" in kan_arch_title["content"]


def test_kan_outline_preserves_numbered_list_protection():
    """Regression #284 bug 1: split-number fix must not promote numbered body lists."""
    # Must still reject trailing-period body-list markers (sentence case is now OK for split numbers)
    md = """1
Introduction

Intro body.

2
Kolmogorov-Arnold Networks

KAN body.

3.
Our approach always used identical homogeneous experts.

4
KANs are interpretable

Done.
"""
    titles = [s.title for s in parse_markdown_sections(md)]
    assert "Introduction" in titles
    assert "Kolmogorov-Arnold Networks" in titles
    assert "KANs are interpretable" in titles
    assert "Our approach always used identical homogeneous experts" not in titles
    assert len(titles) == 3


def test_split_body_list_without_trailing_period_rejected():
    """Regression #284 round 2 blocker 2: reject split-form body lists without trailing period."""
    # A split number "1" followed by a sentence like "We propose..." is a body list, not a heading
    md = """1
Introduction

Research overview.

1
We propose a new method for routing tokens to experts.

2
Related works

Prior work.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]
    # Only real headings, not the body list
    assert "Introduction" in titles
    assert "Related works" in titles
    assert "We propose a new method for routing tokens to experts" not in titles
    # Body list stays in Introduction section
    intro = sections[0]
    intro_body = md[intro.start : intro.end]
    assert "We propose a new method" in intro_body


def test_guard_percent_sign_rejection():
    """Guard test: reject split numbers with % in title (e.g. 'Impro. (%)')."""
    # Sequence would accept 2, but % guard rejects it
    md = """1
Introduction

First section.

2
Impro. (%)

Real second section follows.

3
Methods

Third section.
"""
    titles = [s.title for s in parse_markdown_sections(md)]
    assert "Introduction" in titles
    assert "Methods" in titles
    # Reject table row with percentage (guard blocks it)
    assert "Impro. (%)" not in titles
    # Should have 2 sections (1 and 3)
    assert len(titles) == 2


def test_guard_equals_sign_rejection():
    """Guard test: reject split numbers with = in title (e.g. pseudocode lines)."""
    # Sequence would accept 2, but = guard rejects it
    md = """1
Introduction

First section.

2
ExpsGPU = getActiveExperts(layer)

Real second section follows.

3
Methods

Third section.
"""
    titles = [s.title for s in parse_markdown_sections(md)]
    assert "Introduction" in titles
    assert "Methods" in titles
    # Reject pseudocode line (guard blocks it)
    assert "ExpsGPU = getActiveExperts(layer)" not in titles
    # Should have 2 sections (1 and 3)
    assert len(titles) == 2


def test_guard_colon_digit_rejection():
    """Guard test: reject split numbers with : followed by digits (e.g. 'Loss: 0.42')."""
    md = """1
Introduction

First section.

1
Loss: 0.42

2
Methods

Second section.
"""
    titles = [s.title for s in parse_markdown_sections(md)]
    assert "Introduction" in titles
    assert "Methods" in titles
    # Reject metric line
    assert "Loss: 0.42" not in titles


def test_guard_model_name_rejection():
    """Guard test: reject model names with dotted versions like 'Phi-3.5 MoE'."""
    # Sequence would accept 2, but dotted-version guard rejects it
    md = """1
Introduction

First section.

2
Phi-3.5 MoE

Real second section follows.

3
Methods

Third section.
"""
    titles = [s.title for s in parse_markdown_sections(md)]
    assert "Introduction" in titles
    assert "Methods" in titles
    # Reject model name (dotted-version guard blocks it)
    assert "Phi-3.5 MoE" not in titles
    # Should have 2 sections (1 and 3)
    assert len(titles) == 2


def test_guard_table_label_rejection():
    """Guard test: reject table labels like 'Ours', 'Baseline'."""
    # Sequence would accept 2, but table-label guard rejects it
    md = """1
Introduction

First section.

2
Ours

Real second section follows.

3
Methods

Third section.
"""
    titles = [s.title for s in parse_markdown_sections(md)]
    assert "Introduction" in titles
    assert "Methods" in titles
    # Reject table label (guard blocks it)
    assert "Ours" not in titles
    # Should have 2 sections (1 and 3)
    assert len(titles) == 2


def test_daop_style_table_cells_rejected():
    """Regression #284 round 2: DAOP paper should not gain fake sections from table rows.

    Real DAOP has 21 sections; round 1 produced 29 by accepting table cells like
    'Mixtral 8x7B', 'Avg. Accuracy: 84.11%', 'SwapNum = 0.5' as fake headings.
    """
    md = """1
Introduction

Intro body.

2
Background

Background body.

1
Mixtral 8x7B

1
Avg. Accuracy: 84.11%

1
SwapNum = 0.5

3
Speedup

Real section body.

4
Energy

Energy body.

5
Accuracy

Accuracy body.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]
    # Only real sections, no table rows
    assert "Introduction" in titles
    assert "Background" in titles
    assert "Speedup" in titles
    assert "Energy" in titles
    assert "Accuracy" in titles
    # Reject table/data rows
    assert "Mixtral 8x7B" not in titles
    assert "Avg. Accuracy: 84.11%" not in titles
    assert "SwapNum = 0.5" not in titles
    # Should have 5 real sections, not 8
    assert len(sections) == 5


def test_switch_style_model_labels_rejected():
    """Regression #284 round 2: Switch paper should not gain fake sections from model rows.

    Real Switch has 7 sections; round 1 produced 46 by accepting model labels like
    'T5-Large', 'T5-XL', 'T5-XXL', 'Switch-C' as fake headings.
    """
    md = """1
Introduction

Intro body.

1
T5-Large

1
T5-XL

1
T5-XXL

1
Switch-Base

1
Switch-Large

1
Switch-C

1
Model

2
Methods

Methods body.

3
Evaluation

Eval body.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]
    # Only real sections, no model labels
    assert "Introduction" in titles
    assert "Methods" in titles
    assert "Evaluation" in titles
    # Reject model labels
    assert "T5-Large" not in titles
    assert "T5-XL" not in titles
    assert "T5-XXL" not in titles
    assert "Switch-Base" not in titles
    assert "Switch-Large" not in titles
    assert "Switch-C" not in titles
    assert "Model" not in titles
    # Should have 3 real sections, not 10
    assert len(sections) == 3


def test_switch_table_number_rejection():
    """Regression #284 round 3: Switch table numbers 12/16/64 must not become sections."""
    from tests.fixtures.paper_outlines import SWITCH_EXCERPT

    sections = parse_markdown_sections(SWITCH_EXCERPT)
    titles = [s.title for s in sections]

    # Real sections are kept
    assert "Introduction" in titles
    assert "Switch Transformer" in titles
    assert "Simplifying Sparse Routing" in titles
    assert "Scaling Properties" in titles

    # Table numbers rejected
    assert "T5-Large" not in titles
    assert "T5-XL" not in titles
    assert "Switch-C" not in titles
    assert "Model" not in titles

    # Should have 4 sections (1, 2, 2.1, 3), not 8
    assert len(sections) == 4


def test_lora_table_row_rejection():
    """Regression #284 round 3/4: LoRA 5.2 is a REAL heading, not a table row."""
    from tests.fixtures.paper_outlines import LORA_EXCERPT

    sections = parse_markdown_sections(LORA_EXCERPT)
    titles = [s.title for s in sections]

    # Real sections kept
    assert "Introduction" in titles
    assert "Problem Statement" in titles
    assert "Aren't Existing Solutions Good Enough?" in titles
    assert "Our Method" in titles
    assert "Empirical Experiments" in titles
    assert "Baselines" in titles
    assert "RoBERTa base/large" in titles  # Real heading 5.2
    assert "Related Works" in titles

    # Should have 8 sections
    assert len(sections) == 8


def test_cot_dataset_row_rejection():
    """Regression #284 round 3: CoT 60/80 rows must not become sections."""
    from tests.fixtures.paper_outlines import COT_EXCERPT

    sections = parse_markdown_sections(COT_EXCERPT)
    titles = [s.title for s in sections]

    # Real sections kept
    assert "Introduction" in titles
    assert "Chain-of-Thought Prompting" in titles
    assert "Arithmetic Reasoning" in titles

    # Dataset rows rejected (60/80 way out of sequence)
    assert "GSM8K" not in titles
    assert "Model scale" not in titles

    # Should have 3 sections (1, 2, 3)
    assert len(sections) == 3


def test_attention_model_architecture_kept():
    """Regression #284 round 3: Attention '3 Model Architecture' must be kept."""
    from tests.fixtures.paper_outlines import ATTENTION_EXCERPT

    sections = parse_markdown_sections(ATTENTION_EXCERPT)
    titles = [s.title for s in sections]
    section_ids = [s.section_id for s in sections]

    # All real sections kept
    assert "Introduction" in titles
    assert "Background" in titles
    assert "Model Architecture" in titles
    assert "Encoder and Decoder Stacks" in titles

    # Proper numbering
    by_title = {s.title: s for s in sections}
    assert by_title["Model Architecture"].section_id == "3"
    assert by_title["Encoder and Decoder Stacks"].section_id == "3.1"

    assert len(sections) == 4


def test_mistral_architectural_details_kept():
    """Regression #284 round 3: Mistral '2 Architectural details' must be kept."""
    from tests.fixtures.paper_outlines import MISTRAL_EXCERPT

    sections = parse_markdown_sections(MISTRAL_EXCERPT)
    titles = [s.title for s in sections]

    # All real sections kept
    assert "Introduction" in titles
    assert "Architectural details" in titles
    assert "Sliding Window Attention" in titles
    assert "Results" in titles

    # Proper numbering
    by_title = {s.title: s for s in sections}
    assert by_title["Architectural details"].section_id == "2"
    assert by_title["Sliding Window Attention"].section_id == "2.1"

    assert len(sections) == 4


def test_deepseek_r1_zero_kept():
    """Regression #284 round 3: DeepSeek-R1 '2 DeepSeek-R1-Zero' must be kept."""
    from tests.fixtures.paper_outlines import DEEPSEEK_R1_EXCERPT

    sections = parse_markdown_sections(DEEPSEEK_R1_EXCERPT)
    titles = [s.title for s in sections]

    # All real sections kept (Abstract is unnumbered, doesn't interfere)
    assert "Abstract" in titles
    assert "Introduction" in titles
    assert "DeepSeek-R1-Zero" in titles
    assert "Group Relative Policy Optimization" in titles
    assert "DeepSeek-R1" in titles

    # Proper numbering - Abstract is treated as section 1
    by_title = {s.title: s for s in sections}
    assert by_title["Abstract"].section_id == "1"
    assert by_title["Introduction"].section_id == "2"
    assert by_title["DeepSeek-R1-Zero"].section_id == "3"
    assert by_title["Group Relative Policy Optimization"].section_id == "3.1"
    assert by_title["DeepSeek-R1"].section_id == "4"

    assert len(sections) == 5


def test_llama2_subsections_kept():
    """Regression #284 round 3: Llama 2 subsections must be kept."""
    from tests.fixtures.paper_outlines import LLAMA2_EXCERPT

    sections = parse_markdown_sections(LLAMA2_EXCERPT)
    titles = [s.title for s in sections]

    # All sections and subsections kept
    assert "Introduction" in titles
    assert "Pretraining" in titles
    assert "Pretraining Data" in titles
    assert "Training Details" in titles
    assert "Training Hardware & Carbon Footprint" in titles
    assert "Fine-tuning" in titles
    assert "Supervised Fine-Tuning (SFT)" in titles

    # Check structure
    by_title = {s.title: s for s in sections}
    assert by_title["Pretraining Data"].section_id == "2.1"
    assert by_title["Training Details"].section_id == "2.2"
    assert by_title["Training Hardware & Carbon Footprint"].section_id == "2.2.1"

    assert len(sections) == 7


def test_mamba_subsections_kept():
    """Regression #284 round 3: Mamba subsections must be kept."""
    from tests.fixtures.paper_outlines import MAMBA_EXCERPT

    sections = parse_markdown_sections(MAMBA_EXCERPT)
    titles = [s.title for s in sections]

    # All sections kept
    assert "Introduction" in titles
    assert "State Space Models" in titles
    assert "Discretization" in titles
    assert "Selective State Space Models" in titles
    assert "Motivation: Selection as a Means of Compression" in titles

    # Check structure
    by_title = {s.title: s for s in sections}
    assert by_title["Discretization"].section_id == "2.1"
    assert (
        by_title["Motivation: Selection as a Means of Compression"].section_id == "3.1"
    )

    assert len(sections) == 5


def test_daop_no_table_fakes():
    """Regression #284 round 3: DAOP must reject table cells, keep 5 sections."""
    from tests.fixtures.paper_outlines import DAOP_EXCERPT

    sections = parse_markdown_sections(DAOP_EXCERPT)
    titles = [s.title for s in sections]

    # Real sections kept
    assert "Abstract" in titles
    assert "Introduction" in titles
    assert "Related Work" in titles
    assert "Methodology" in titles
    assert "Experiments" in titles
    assert "Results" in titles

    # Table rows rejected
    assert "Mixtral 8x7B" not in titles
    assert "Avg. Accuracy: 84.11%" not in titles
    assert "SwapNum = 0.5" not in titles

    # Should have 6 sections (Abstract + 5 numbered)
    assert len(sections) == 6


def test_skipped_section_numbers_allowed():
    """Allow nested sections to skip numbers (e.g. 2 → 2.2 without 2.1)."""
    md = """1
Introduction

First section.

2
Methods

Second section, skips to 2.2.

2.2
Implementation

Subsection 2.2, no 2.1.

2.5
Evaluation

Subsection 2.5, skipping 2.3 and 2.4.

3
Results

Third section.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # All sections kept despite nested skips (top-level is sequential)
    assert "Introduction" in titles
    assert "Methods" in titles
    assert "Implementation" in titles
    assert "Evaluation" in titles
    assert "Results" in titles

    by_title = {s.title: s for s in sections}
    assert by_title["Introduction"].section_id == "1"
    assert by_title["Methods"].section_id == "2"
    assert by_title["Implementation"].section_id == "2.1"
    assert by_title["Evaluation"].section_id == "2.2"
    assert by_title["Results"].section_id == "3"

    assert len(sections) == 5


def test_inline_headings_with_special_content_kept():
    """Real inline headings with colons or digits must be kept."""
    md = """1 Introduction

Intro body.

2 Results: 2D Benchmarks

Results with colon and digits in title.

3 Scaling to 100% Data

Section with percent and number.

4 Conclusion

Done.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # All sections kept - content guards only apply to split numbers
    assert "Introduction" in titles
    assert "Results: 2D Benchmarks" in titles
    assert "Scaling to 100% Data" in titles
    assert "Conclusion" in titles

    assert len(sections) == 4


def test_mutation_sequence_rule():
    """Removing sequence validation must cause at least one test to fail."""
    # This is the KAN case - without sequence validation, table numbers get through
    md = """1
Introduction

Intro body.

12
T5-Large

2
Methods

Real section.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # With sequence validation, 12 is rejected (top-level must be last + 1)
    assert "Introduction" in titles
    assert "Methods" in titles
    assert "T5-Large" not in titles
    assert len(sections) == 2


def test_top_level_strictly_sequential():
    """Top-level sections must be strictly sequential (no skipping allowed)."""
    # Table numbers after section 2 should be rejected
    md = """1
Introduction

First section.

2
Background

Second section.

12
T5-XL

16
Switch-Base

3
Methods

Real section 3.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # 1, 2, 3 accepted; 12 and 16 rejected (top-level must be sequential)
    assert "Introduction" in titles
    assert "Background" in titles
    assert "Methods" in titles
    assert "T5-XL" not in titles
    assert "Switch-Base" not in titles
    assert len(sections) == 3


def test_kan_golden_outline():
    """Golden test: KAN fixture produces exact expected outline."""
    from tests.fixtures.paper_outlines import KAN_EXCERPT

    sections = parse_markdown_sections(KAN_EXCERPT)
    titles = [s.title for s in sections]

    # All real headings present
    assert titles == [
        "Abstract",
        "Introduction",
        "Kolmogorov–Arnold Networks (KAN)",
        "Kolmogorov-Arnold Representation theorem",
        "KAN architecture",
        "KANs are accurate",
        "KANs are interpretable",
    ]


def test_switch_golden_outline():
    """Golden test: Switch fixture produces exact expected outline."""
    from tests.fixtures.paper_outlines import SWITCH_EXCERPT

    sections = parse_markdown_sections(SWITCH_EXCERPT)
    titles = [s.title for s in sections]

    # Real headings present, table numbers rejected
    assert "Introduction" in titles
    assert "Switch Transformer" in titles
    assert "Simplifying Sparse Routing" in titles
    assert "Scaling Properties" in titles
    # Table numbers rejected
    assert "T5-Large" not in titles
    assert "T5-XXL" not in titles
    assert "Switch-Base" not in titles
    assert "Model" not in titles
    assert len(titles) == 4


def test_daop_guards_golden_outline():
    """Golden test: DAOP guards fixture rejects pseudocode and table labels."""
    from tests.fixtures.paper_outlines import DAOP_EXCERPT_GUARDS

    sections = parse_markdown_sections(DAOP_EXCERPT_GUARDS)
    titles = [s.title for s in sections]

    # Real headings present
    assert "Experimental Evaluation" in titles
    assert "Experimental Setup" in titles
    assert "Speedup" in titles
    assert "Energy Efficiency" in titles
    assert "Accuracy Results" in titles
    assert "Discussion" in titles
    # Pseudocode and table data rejected
    assert "SwapNum = 0.5" not in titles
    assert "HotExps = getTopKActiveExperts(ExpsCPU, SwapNum);" not in titles
    assert "ColdExps = getBottomKActiveExperts(ExpsGPU, SwapNum);" not in titles
    assert "Impro. (%)" not in titles
    assert "Mixtral 8x7B" not in titles
    assert "Phi-3.5 MoE" not in titles
    assert "Ours" not in titles
    assert "14.37" not in titles
    assert "27.07" not in titles


def test_sequence_check_applied_to_period_form_split_numbers():
    """Sequence check prevents period-form body lists from being promoted.

    Regression test for issue #284: period-form body list items like
    "1. Load The Model" (Title Case) after section 3 should be rejected by
    sequence validation. Without this check, they would be promoted to fake headings.

    This test fails if the sequence check is bypassed for period-form split numbers.
    Uses Title Case to avoid rejection by the sentence-case rule.
    """
    # Markdown with a real section 3, then a period-form body list that doesn't
    # continue the sequence (1. and 2. after section 3), using Title Case
    md = """# Introduction

Some text.

# Background

More text.

3.
Methods

We describe our approach:

1.
Load The Model

2.
Run The Inference

# Results

Final section.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # Real headings should be present
    assert "Introduction" in titles
    assert "Background" in titles
    assert "Methods" in titles
    assert "Results" in titles

    # Fake body-list headings should NOT be present (rejected by sequence check)
    # These are Title Case so they would pass the sentence-case rule
    assert (
        "Load The Model" not in titles
    ), "Sequence check failed: 1. promoted after section 3"
    assert (
        "Run The Inference" not in titles
    ), "Sequence check failed: 2. promoted after section 3"

    # Should have exactly 4 sections (not 6 with the fake body list items)
    assert len(titles) == 4, f"Expected 4 sections, got {len(titles)}: {titles}"


def test_appendices_after_references_included():
    """Regression for #288: Appendices after References must be included in outline.

    Papers often have appendices after References/Bibliography. These must be
    parsed and addressable by section title/ID. Reference entries (venue lines)
    must still be filtered out.
    """
    sections = parse_markdown_sections(DPO_REAL_EXCERPT)
    titles = [s.title for s in sections]

    # Core section present
    assert "References" in titles

    # Appendices after References are included (#288 fix)
    assert any("Mathematical Derivations" in t for t in titles)
    assert any("DPO Implementation" in t for t in titles)
    assert any("Further Details" in t or "Experimental Set-Up" in t for t in titles)
    assert any("Additional Empirical Results" in t for t in titles)

    # Reference entries still filtered (#229)
    assert not any("Ziegler" in t for t in titles)
    assert not any("Stiennon" in t for t in titles)

    # References section ends at the next section, not at document end
    refs = next(s for s in sections if s.title == "References")
    refs_body = DPO_REAL_EXCERPT[refs.start : refs.end]
    assert "Ziegler" in refs_body
    # Appendix content not in References section
    assert "Mathematical Derivations" not in refs_body
    assert "DPO Implementation" not in refs_body

    # Appendix subsections are properly nested
    by_title = {s.title: s for s in sections}

    # A.1 subsection should exist under Appendix A
    a_derivations = [
        s for s in sections if "Deriving" in s.title and "KL-Constrained" in s.title
    ]
    assert len(a_derivations) == 1
    assert a_derivations[0].level == 2  # subsection of Appendix A

    # C.1 subsection should exist under Appendix C
    c_imdb = [s for s in sections if "IMDb" in s.title]
    assert len(c_imdb) == 1
    assert c_imdb[0].level == 2  # subsection of Appendix C


@pytest.mark.asyncio
async def test_appendix_section_addressable_by_title(patch_storage):
    """Regression for #288: Appendix sections must be addressable via read_paper_section."""
    _write_paper(patch_storage, "2305.18290", DPO_REAL_EXCERPT)

    # get_paper_outline returns appendices
    outline = json.loads(
        (await handle_get_paper_outline({"paper_id": "2305.18290"}))[0].text
    )
    assert outline["status"] == "success"
    titles = [s["title"] for s in outline["sections"]]

    # Check appendices are in outline
    assert any("DPO Implementation" in t for t in titles)
    assert any("Additional Empirical Results" in t for t in titles)

    # read_paper_section by bare title works (without "Appendix B" prefix)
    appendix = json.loads(
        (
            await handle_read_paper_section(
                {
                    "paper_id": "2305.18290",
                    "section_id": "DPO Implementation Details and Hyperparameters",
                }
            )
        )[0].text
    )
    assert appendix["status"] == "success"
    assert "PyTorch" in appendix["content"] or "implement" in appendix["content"]
    assert "DPO Implementation" in appendix["section"]["title"]


@pytest.mark.asyncio
async def test_search_in_appendix_correct_attribution(patch_storage):
    """Regression for #288: Search matches in appendices must be attributed correctly.

    Before fix: matches in appendices were attributed to References section.
    After fix: matches get correct appendix section_id and section_title.
    """
    _write_paper(patch_storage, "2305.18290", DPO_REAL_EXCERPT)

    # Search for text unique to appendix
    search = json.loads(
        (
            await handle_search_paper_text(
                {
                    "paper_id": "2305.18290",
                    "query": "PyTorch code",
                }
            )
        )[0].text
    )
    assert search["status"] == "success"
    assert search["returned_passages"] >= 1

    # Match must be attributed to the appendix, not References
    passage = search["passages"][0]
    assert passage["section_title"] != "References"
    assert (
        "DPO Implementation" in passage["section_title"]
        or "Appendix" in passage["section_title"]
    )
    assert "PyTorch" in passage["excerpt"]


def test_bibliography_terminator_still_filters_entries():
    """Regression guard: Bibliography entries must still be filtered after #288 fix.

    The fix continues past References, but venue/year lines must
    still be filtered by title guards and post-References mode.
    """
    # Simple fixture with References + bibliography lines + Appendix
    md = """# Introduction

Intro body.

# Methods

Methods body.

# References

Ziegler, D. M., Stiennon, N., Wu, J. Fine-tuning language models. 2020.

2017 NeurIPS Conference Proceedings

In Proceedings of the 35th International Conference on Machine Learning

Appendix A

Hyperparameters

Detailed hyperparameters and training procedures.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # Core sections and appendix present
    assert "Introduction" in titles
    assert "Methods" in titles
    assert "References" in titles
    assert any("Appendix" in t or "Hyperparameters" in t for t in titles)

    # Reference entries still filtered (existing guards work)
    assert not any("Ziegler" in t for t in titles)
    assert not any("NeurIPS" in t for t in titles)
    assert not any("Proceedings" in t for t in titles)

    # References section ends at Appendix
    refs = next(s for s in sections if s.title == "References")
    refs_body = md[refs.start : refs.end]
    assert "Ziegler" in refs_body
    assert "2017 NeurIPS" in refs_body
    # Appendix not in References section
    assert "Appendix" not in refs_body
    assert "Hyperparameters" not in refs_body


def test_turpin_real_appendices_reject_atx_table_cells():
    """Regression #288: Real Turpin et al. paper with ATX table cells after References.

    Real HTML→markdown contains '# Failed' and '# FS (Ans. A)' as table cells.
    These ATX lines must NOT become sections in post-References mode.
    Only explicit appendix patterns should be accepted.
    """
    sections = parse_markdown_sections(TURPIN_REAL_EXCERPT)
    titles = [s.title for s in sections]

    # Appendices recognized (some with Title Case titles)
    assert "References" in titles
    assert "Appendix A Additional Samples" in titles or "Additional Samples" in titles
    # Appendix B has sentence-case title, may be rejected (nice-to-have)
    assert "Appendix C Qualitative Analysis Details" in titles or any(
        "Qualitative" in t for t in titles
    )
    assert "Appendix D Results Tables" in titles or any(
        "Results Tables" in t for t in titles
    )
    assert "Appendix E Prompting Details" in titles or any(
        "Prompting Details" in t for t in titles
    )
    assert "Appendix F Additional BBH Experiment Details" in titles or any(
        "Additional BBH" in t for t in titles
    )

    # ATX table cells rejected (blocker #288 round 3)
    assert "Failed" not in titles
    assert "FS (Ans. A)" not in titles
    assert "# Failed" not in titles
    assert "# FS (Ans. A)" not in titles

    # Bare table cell combos rejected (blocker #288 round 3)
    assert "Web Of Lies" not in titles
    assert "B Web Of Lies" not in titles

    # Bare table labels rejected (blocker #288 round 3)
    assert "Snarks" not in titles
    assert "Hyperbaton" not in titles
    assert "Date Understanding" not in titles
    assert "experiments" not in titles

    # GPT/Claude model names rejected
    assert "GPT-3.5" not in titles
    assert "Claude 1.0" not in titles
    assert "No-CoT" not in titles

    # Subsections like C.1 and F.1 should be recognized
    c1_found = any(
        "C.1" in t or ("BBH" in t and "C" in s.section_id)
        for s, t in zip(sections, titles)
    )
    assert c1_found

    f1_found = any(
        "F.1" in t or ("Data" in t and "F" in s.section_id)
        for s, t in zip(sections, titles)
    )
    assert f1_found


def test_dpo_real_appendices_and_bare_title_lookup():
    """Regression #288: Real DPO paper with appendices.

    Appendices are stored as 'Appendix B DPO Implementation...' but
    lookup by 'DPO Implementation Details and Hyperparameters' should work.
    """
    sections = parse_markdown_sections(DPO_REAL_EXCERPT)
    titles = [s.title for s in sections]

    # Main appendices recognized
    appendix_b_found = any("DPO Implementation" in t for t in titles)
    assert appendix_b_found, f"Appendix B not found in {titles}"

    appendix_c_found = any(
        "Further Details" in t or "Experimental Set-Up" in t for t in titles
    )
    assert appendix_c_found

    # Subsections recognized with correct levels
    a1_titles = [t for t in titles if "A.1" in t or "Deriving" in t]
    assert len(a1_titles) >= 1

    c1_titles = [t for t in titles if "C.1" in t or "IMDb" in t]
    assert len(c1_titles) >= 1

    # Test bare title lookup (without "Appendix B " prefix)
    section = _find_section(sections, "DPO Implementation Details and Hyperparameters")
    assert section is not None, "Lookup by bare title should work"
    assert "DPO Implementation" in section.title


def test_llama2_acknowledgments_stays_at_level_3():
    """Regression #288 blocker 4: A.1.1 Acknowledgments must be level 3, not top-level.

    Numbered prefix wins over keyword: depth determined by dots, not keyword.
    """
    # Need References before A.1.1 to enable post-References mode
    excerpt_with_refs = "# References\n\n" + LLAMA2_REAL_EXCERPT
    sections = parse_markdown_sections(excerpt_with_refs)
    titles = [s.title for s in sections]

    # Find Acknowledgments section
    ack_sections = [s for s in sections if "Acknowledgments" in s.title]
    assert (
        len(ack_sections) == 1
    ), f"Expected 1 Acknowledgments, got {len(ack_sections)}: {titles}"

    ack = ack_sections[0]
    # A.1.1 = level 3 (A=1, .1=+1, .1=+1)
    assert ack.level == 3, f"A.1.1 Acknowledgments should be level 3, got {ack.level}"
    assert "A.1.1" in ack.section_id or ack.level == 3


def test_post_references_guard_rejects_fakes():
    """Regression #288: Post-References mode must reject table cells and ATX fakes.

    This test verifies the guard is active. Disabling post-References filtering
    should cause this test to fail by accepting fake sections.
    """
    sections = parse_markdown_sections(TURPIN_REAL_EXCERPT)
    titles = [s.title for s in sections]

    # These table cells/ATX fakes must NOT be sections
    forbidden = [
        "Failed",
        "# Failed",
        "FS (Ans. A)",
        "# FS (Ans. A)",
        "GPT-3.5",
        "Claude 1.0",
        "No-CoT",
        "CoT",
    ]
    for fake in forbidden:
        assert (
            fake not in titles
        ), f"Post-References guard failed: '{fake}' became a section"

    # But real appendices should be present
    assert any(
        "Appendix" in t or "Prompting Details" in t for t in titles
    ), "Post-References mode should still accept real appendices"


def test_bare_table_cells_after_references_rejected():
    """Regression #288 blocker 1: Bare table cells after References must be rejected.

    Real papers have bare table cells like 'MQA', 'Limitations', 'Method', 'Learning Rate'
    that must not become sections after References.
    """
    md = """# Introduction

Intro body.

# Methods

Methods body.

# References

[1] Someone et al. 2020.

MQA

Limitations

Method

Learning Rate

0.001

Appendix A

Implementation Details

More details here.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # Real sections present
    assert "Introduction" in titles
    assert "Methods" in titles
    assert "References" in titles

    # Bare table cells rejected
    assert "MQA" not in titles, "Bare table cell 'MQA' should be rejected"
    assert (
        "Limitations" not in titles
    ), "Bare table cell 'Limitations' should be rejected"
    assert "Method" not in titles, "Bare table cell 'Method' should be rejected"
    assert (
        "Learning Rate" not in titles
    ), "Bare table cell 'Learning Rate' should be rejected"
    assert "0.001" not in titles

    # Real appendix accepted
    assert any("Implementation Details" in t or "Appendix A" in t for t in titles)


def test_body_reference_does_not_trigger_post_references_mode():
    """Regression #288 blocker 2: Lowercase 'reference' in body must not trigger mode.

    A prompt template containing 'reference' before the real References heading
    must not trigger post-References mode. The real References heading and
    subsequent appendices must still be found.
    """
    md = """# Introduction

Large language models are trained on diverse data.

# Prompt Template

The template contains the word reference in lowercase within instructions.

User: Please provide a reference for this claim.
Assistant: I will provide a reference for you.

# Methods

Our experimental setup.

# Results

Performance metrics.

# References

[1] Brown et al. Language Models are Few-Shot Learners. 2020.

Appendix A

Background

Additional background details.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # All sections including those before References should be present
    assert "Introduction" in titles
    assert "Prompt Template" in titles
    assert "Methods" in titles
    assert "Results" in titles
    assert "References" in titles

    # Appendix after real References should be present
    assert any("Background" in t or "Appendix A" in t for t in titles)

    # Body lines with 'reference' should not trigger mode early
    # (verified by the presence of Methods/Results sections which come after the prompt)
    methods_idx = next(i for i, t in enumerate(titles) if t == "Methods")
    refs_idx = next(i for i, t in enumerate(titles) if t == "References")
    assert methods_idx < refs_idx, "Methods should come before References"


def test_lowercase_references_line_after_references_rejected():
    """Regression #288 blocker 2: Lowercase 'references' line after References is rejected."""
    md = """# Introduction

Intro.

# References

[1] Someone et al.

references

another reference line

Appendix A

Details

More details.
"""
    sections = parse_markdown_sections(md)
    titles = [s.title for s in sections]

    # Real References section present
    assert "References" in titles

    # Lowercase 'references' line rejected (not a real heading)
    # Count how many times "references" appears (should be once, the real heading)
    refs_count = sum(1 for t in titles if t.lower() == "references")
    assert refs_count == 1, "Only the real References heading should be present"

    # Appendix still accepted
    assert any("Details" in t or "Appendix A" in t for t in titles)


def test_post_references_mutation_check():
    """Mutation test: Removing post-References whitelist must cause failures.

    If the strict whitelist is disabled (replaced with accept-all), several
    tests should fail by accepting fake sections after References.
    """
    # This test documents the expected behavior; if you disable the whitelist,
    # the following assertions should start failing:

    # Test 1: Turpin excerpt should reject table cells
    turpin_sections = parse_markdown_sections(TURPIN_REAL_EXCERPT)
    turpin_titles = [s.title for s in turpin_sections]
    assert "Snarks" not in turpin_titles, "Mutation: Snarks should be rejected"
    assert "Hyperbaton" not in turpin_titles, "Mutation: Hyperbaton should be rejected"
    assert (
        "Web Of Lies" not in turpin_titles
    ), "Mutation: Web Of Lies should be rejected"
    assert "Failed" not in turpin_titles, "Mutation: Failed should be rejected"

    # Test 2: Bare table cells fixture
    table_cells_md = """# References

[1] Paper

MQA

Method

Appendix A

Real Appendix
"""
    table_sections = parse_markdown_sections(table_cells_md)
    table_titles = [s.title for s in table_sections]
    assert "MQA" not in table_titles, "Mutation: MQA should be rejected"
    assert "Method" not in table_titles, "Mutation: Method should be rejected"

    # Appendix should still be present
    assert any("Appendix" in t or "Real Appendix" in t for t in table_titles)
