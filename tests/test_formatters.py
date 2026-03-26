"""Tests for shared formatting utilities."""

import pytest

from research_mcp_server.utils.formatters import (
    format_bibtex_entry,
    format_paper_json,
    format_paper_markdown,
    format_papers_table,
    generate_bibtex_key,
    truncate_abstract,
)


# --- truncate_abstract ---


def test_truncate_abstract_short():
    """Abstract under max_chars is returned unchanged."""
    short = "This is a short abstract."
    assert truncate_abstract(short, max_chars=300) == short


def test_truncate_abstract_at_sentence():
    """Truncation happens at the last sentence boundary within max_chars."""
    # Place a sentence boundary past the 50% mark of max_chars so rfind picks it up
    # "x" * 55 + ". " puts the period at index 55, which is > 50% of 100
    abstract = "x" * 55 + ". " + "y" * 200
    result = truncate_abstract(abstract, max_chars=100)
    # Should end at the period (sentence boundary)
    assert result.endswith(".")
    assert len(result) <= 100


def test_truncate_abstract_no_sentence():
    """When no sentence boundary exists past the midpoint, truncate with ellipsis."""
    abstract = "a " * 200  # No periods at all
    result = truncate_abstract(abstract, max_chars=100)
    assert result.endswith("...")
    assert len(result) <= 103  # 100 chars + "..."


# --- generate_bibtex_key ---


def test_generate_bibtex_key():
    """Key format is {lastname}{year}{firstword}."""
    key = generate_bibtex_key("Alice Johnson", 2024, "Neural Networks for NLP")
    assert key == "johnson2024neural"


def test_generate_bibtex_key_stopwords():
    """Skips stopwords in the title to find the first significant word."""
    key = generate_bibtex_key("Bob Smith", 2023, "On the Analysis of Graphs")
    assert key == "smith2023analysis"


# --- format_bibtex_entry ---


def test_format_bibtex_entry():
    """Produces valid BibTeX output with all expected fields."""
    paper = {
        "id": "2401.12345",
        "title": "Test Paper on LLMs",
        "authors": ["Alice Johnson", "Bob Smith"],
        "published": "2024-01-15",
        "categories": ["cs.AI", "cs.CL"],
    }
    result = format_bibtex_entry(paper)
    assert result.startswith("@article{johnson2024test,")
    assert "title={Test Paper on LLMs}" in result
    assert "author={Alice Johnson and Bob Smith}" in result
    assert "year={2024}" in result
    assert "eprint={2401.12345}" in result
    assert "archivePrefix={arXiv}" in result
    assert "primaryClass={cs.AI}" in result
    assert result.strip().endswith("}")


# --- format_paper_markdown ---


def test_format_paper_markdown():
    """Markdown output includes title, authors, and categories."""
    paper = {
        "id": "2401.12345",
        "title": "Test Paper on LLMs",
        "authors": ["Alice", "Bob"],
        "abstract": "A short abstract.",
        "categories": ["cs.AI", "cs.CL"],
        "published": "2024-01-15",
        "url": "https://arxiv.org/pdf/2401.12345",
    }
    result = format_paper_markdown(paper)
    assert "### Test Paper on LLMs" in result
    assert "Alice, Bob" in result
    assert "cs.AI, cs.CL" in result
    assert "2024-01-15" in result
    assert "https://arxiv.org/pdf/2401.12345" in result


# --- format_paper_json ---


def test_format_paper_json():
    """JSON output maps keys correctly."""
    paper = {
        "id": "2401.12345",
        "title": "Test Paper",
        "authors": ["Alice"],
        "abstract": "Abstract text.",
        "categories": ["cs.AI"],
        "published": "2024-01-15",
        "url": "https://arxiv.org/pdf/2401.12345",
        "citation_count": 42,
    }
    result = format_paper_json(paper)
    assert result["paper_id"] == "2401.12345"
    assert result["title"] == "Test Paper"
    assert result["authors"] == ["Alice"]
    assert result["abstract"] == "Abstract text."
    assert result["categories"] == ["cs.AI"]
    assert result["published"] == "2024-01-15"
    assert result["pdf_url"] == "https://arxiv.org/pdf/2401.12345"
    assert result["citation_count"] == 42


# --- format_papers_table ---


def test_format_papers_table():
    """Table has a header row, a separator row, and data rows."""
    papers = [
        {
            "id": "2401.12345",
            "title": "Test Paper",
            "authors": ["Alice", "Bob"],
            "published": "2024-01-15",
        },
        {
            "id": "2401.67890",
            "title": "Another Paper",
            "authors": ["Charlie"],
            "published": "2024-02-20",
        },
    ]
    columns = ["id", "title", "authors", "published"]
    result = format_papers_table(papers, columns)
    lines = result.split("\n")

    # Header row
    assert "ID" in lines[0]
    assert "Title" in lines[0]
    assert "Authors" in lines[0]
    assert "Published" in lines[0]

    # Separator row with dashes
    assert "---" in lines[1]

    # Data rows
    assert len(lines) == 4  # header + separator + 2 papers
    assert "2401.12345" in lines[2]
    assert "2401.67890" in lines[3]
