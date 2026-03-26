"""Shared formatting functions for consistent output across all tools."""

import json
import re
from typing import Any


def format_paper_markdown(paper: dict[str, Any]) -> str:
    """Format a single paper as a markdown block.

    Args:
        paper: Paper dict with keys: id, title, authors, abstract,
               categories, published, url.

    Returns:
        Formatted markdown string.
    """
    authors_str = ", ".join(paper.get("authors", []))
    categories_str = ", ".join(paper.get("categories", []))
    abstract = truncate_abstract(paper.get("abstract", ""), max_chars=300)

    lines = [
        f"### {paper.get('title', 'Untitled')}",
        f"**ID**: {paper.get('id', 'N/A')}",
        f"**Authors**: {authors_str}",
        f"**Published**: {paper.get('published', 'N/A')}",
        f"**Categories**: {categories_str}",
        f"**PDF**: {paper.get('url', 'N/A')}",
        "",
        abstract,
    ]

    citation_count = paper.get("citation_count")
    if citation_count is not None:
        lines.insert(5, f"**Citations**: {citation_count}")

    return "\n".join(lines)


def format_paper_json(paper: dict[str, Any]) -> dict[str, Any]:
    """Format a single paper as a clean JSON dict.

    Args:
        paper: Raw paper dict.

    Returns:
        Cleaned paper dict with consistent keys.
    """
    return {
        "paper_id": paper.get("id", ""),
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "abstract": paper.get("abstract", ""),
        "categories": paper.get("categories", []),
        "published": paper.get("published", ""),
        "pdf_url": paper.get("url", ""),
        "citation_count": paper.get("citation_count"),
    }


def format_papers_table(papers: list[dict[str, Any]], columns: list[str]) -> str:
    """Format multiple papers as a markdown table.

    Args:
        papers: List of paper dicts.
        columns: Column keys to include. Supported: id, title, authors,
                 published, categories, citation_count.

    Returns:
        Markdown table string.
    """
    column_labels = {
        "id": "ID",
        "title": "Title",
        "authors": "Authors",
        "published": "Published",
        "categories": "Categories",
        "citation_count": "Citations",
    }

    # Header
    header = " | ".join(column_labels.get(c, c) for c in columns)
    separator = " | ".join("---" for _ in columns)
    lines = [header, separator]

    for paper in papers:
        row_vals = []
        for col in columns:
            val = paper.get(col, "")
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val[:3])
                if len(paper.get(col, [])) > 3:
                    val += "..."
            elif col == "title":
                val = str(val)[:60]
                if len(str(paper.get(col, ""))) > 60:
                    val += "..."
            else:
                val = str(val) if val is not None else ""
            row_vals.append(val)
        lines.append(" | ".join(row_vals))

    return "\n".join(lines)


def truncate_abstract(abstract: str, max_chars: int = 300) -> str:
    """Truncate abstract at sentence boundary.

    Args:
        abstract: Full abstract text.
        max_chars: Maximum character length.

    Returns:
        Truncated abstract ending at a sentence boundary.
    """
    if not abstract or len(abstract) <= max_chars:
        return abstract

    # Try to cut at sentence boundary
    truncated = abstract[:max_chars]
    last_period = truncated.rfind(".")
    if last_period > max_chars * 0.5:
        return truncated[: last_period + 1]

    return truncated.rstrip() + "..."


def generate_bibtex_key(first_author: str, year: int, title: str) -> str:
    """Generate a BibTeX citation key.

    Format: {first_author_lastname}{year}{first_significant_title_word}
    All lowercase, no spaces.

    Args:
        first_author: Full name of first author.
        year: Publication year.
        title: Paper title.

    Returns:
        BibTeX key string.
    """
    # Extract last name
    parts = first_author.strip().split()
    lastname = parts[-1] if parts else "unknown"
    lastname = re.sub(r"[^a-zA-Z]", "", lastname).lower()

    # First significant title word (skip common words)
    stopwords = {
        "a", "an", "the", "on", "in", "of", "for", "to", "and", "or",
        "is", "are", "was", "were", "with", "from", "by", "at", "as",
    }
    title_words = re.findall(r"[a-zA-Z]+", title.lower())
    significant = next(
        (w for w in title_words if w not in stopwords), title_words[0] if title_words else "paper"
    )

    return f"{lastname}{year}{significant}"


def format_bibtex_entry(paper: dict[str, Any]) -> str:
    """Format a single paper as BibTeX.

    Args:
        paper: Paper dict with keys: id, title, authors, published, categories.

    Returns:
        BibTeX entry string.
    """
    authors = paper.get("authors", [])
    first_author = authors[0] if authors else "Unknown"

    published = paper.get("published", "")
    year = published[:4] if len(published) >= 4 else "0000"

    title = paper.get("title", "Untitled")
    key = generate_bibtex_key(first_author, int(year), title)

    authors_bibtex = " and ".join(authors)
    categories = paper.get("categories", [])
    primary_class = categories[0] if categories else ""
    paper_id = paper.get("id", "")

    lines = [
        f"@article{{{key},",
        f"    title={{{title}}},",
        f"    author={{{authors_bibtex}}},",
        f"    year={{{year}}},",
        f"    eprint={{{paper_id}}},",
        f"    archivePrefix={{arXiv}},",
    ]
    if primary_class:
        lines.append(f"    primaryClass={{{primary_class}}},")
    lines.append(f"    url={{https://arxiv.org/abs/{paper_id}}}")
    lines.append("}")

    return "\n".join(lines)
