"""Tests for parser regression fixes from PR #285 review."""

import pytest
from arxiv_mcp_server.tools.paper_outline import parse_markdown_sections


def test_baseline_period_form_heading():
    """Period-form heading '2.\\nBaseline' should be kept (P2 fix)."""
    content = (
        "1.\nIntroduction\n\nBody text.\n\n"
        "2.\nBaseline\n\nBaseline body.\n\n"
        "3.\nEvaluation\n\nResult text."
    )
    sections = parse_markdown_sections(content)
    titles = [s.title for s in sections]
    assert titles == ["Introduction", "Baseline", "Evaluation"]


def test_short_inline_heading():
    """Short inline heading '2 RL' should be kept (P2 fix)."""
    content = (
        "1 Introduction\n\nBody text.\n\n"
        "2 RL\n\nReinforcement learning.\n\n"
        "3 Evaluation\n\nResult text."
    )
    sections = parse_markdown_sections(content)
    titles = [s.title for s in sections]
    assert titles == ["Introduction", "RL", "Evaluation"]


def test_body_list_rejected():
    """Body list items '1.\\nLoad the model' should be rejected (P3 fix)."""
    content = (
        "1.\nIntroduction\n\nOur approach has two steps:\n\n"
        "1.\nLoad the model\n\n2.\nRun the inference\n\n"
        "2.\nEvaluation\n\nResult text."
    )
    sections = parse_markdown_sections(content)
    titles = [s.title for s in sections]
    assert titles == ["Introduction", "Evaluation"]


def test_period_form_requires_title_case():
    """Period-form headings require Title Case (P3 fix)."""
    # Period-form with sentence case should be rejected
    content = (
        "1.\nIntroduction\n\nBody text.\n\n"
        "2.\nthis is sentence case\n\nShould be rejected.\n\n"
        "3.\nEvaluation\n\nResult text."
    )
    sections = parse_markdown_sections(content)
    titles = [s.title for s in sections]
    assert "this is sentence case" not in titles


def test_period_less_allows_sentence_case():
    """Period-less split numbers allow sentence case for real cases like KAN (P3 clarification)."""
    content = (
        "1 Introduction\n\nBody text.\n\n"
        "2\nKAN architecture\n\nKolmogorov-Arnold Networks.\n\n"
        "3 Evaluation\n\nResult text."
    )
    sections = parse_markdown_sections(content)
    titles = [s.title for s in sections]
    assert "KAN architecture" in titles


def test_content_guards_not_applied_to_period_form():
    """Content guards (model names, table labels) should not apply to period-form headings (P2 fix)."""
    # Period-form "2.\nBERT" should be kept (Pattern 4 should not apply)
    content = (
        "1.\nIntroduction\n\nBody text.\n\n"
        "2.\nBERT\n\nBidirectional Encoder Representations.\n\n"
        "3.\nEvaluation\n\nResult text."
    )
    sections = parse_markdown_sections(content)
    titles = [s.title for s in sections]
    assert "BERT" in titles

    # Period-form "4.\nOurs" should be kept (Pattern 5 should not apply)
    content2 = (
        "1.\nIntroduction\n\nBody text.\n\n"
        "2.\nBaseline\n\nBaseline body.\n\n"
        "3.\nPrevious\n\nPrevious work.\n\n"
        "4.\nOurs\n\nOur approach.\n\n"
        "5.\nEvaluation\n\nResult text."
    )
    sections2 = parse_markdown_sections(content2)
    titles2 = [s.title for s in sections2]
    assert "Baseline" in titles2
    assert "Previous" in titles2
    assert "Ours" in titles2


def test_sequence_check_applied_to_all_split_numbers():
    """Sequence check should apply to both period-form and period-less split numbers (P3 fix)."""
    # Period-form "1." after "1 Introduction" should be rejected (sibling jump 0)
    content = (
        "1 Introduction\n\nBody text.\n\n"
        "1.\nLoad the model\n\n"
        "2 Evaluation\n\nResult text."
    )
    sections = parse_markdown_sections(content)
    titles = [s.title for s in sections]
    assert "Load the model" not in titles
    assert titles == ["Introduction", "Evaluation"]

    # Period-less "1" after "1. Introduction" should also be rejected
    content2 = (
        "1.\nIntroduction\n\nBody text.\n\n"
        "1\nLoad the model\n\n"
        "2.\nEvaluation\n\nResult text."
    )
    sections2 = parse_markdown_sections(content2)
    titles2 = [s.title for s in sections2]
    assert "Load the model" not in titles2
    assert titles2 == ["Introduction", "Evaluation"]
