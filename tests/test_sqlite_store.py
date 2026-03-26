"""Tests for the SQLite persistence store."""

import pytest

from arxiv_mcp_server.store.sqlite_store import SQLiteStore


SAMPLE_PAPER = {
    "paper_id": "2401.12345",
    "title": "Test Paper on LLMs",
    "authors": ["Alice", "Bob"],
    "abstract": "Testing abstract about language models",
    "categories": ["cs.AI", "cs.CL"],
    "published": "2024-01-15",
    "url": "https://arxiv.org/pdf/2401.12345",
}


@pytest.fixture
def store(tmp_path):
    """Create a SQLiteStore backed by a temporary database."""
    return SQLiteStore(db_path=tmp_path / "test_arxiv_cache.db")


@pytest.mark.asyncio
async def test_upsert_and_get_paper(store):
    """Insert a paper then retrieve it by ID."""
    await store.upsert_paper(SAMPLE_PAPER)
    result = await store.get_paper("2401.12345")

    assert result is not None
    assert result["paper_id"] == "2401.12345"
    assert result["title"] == "Test Paper on LLMs"
    assert result["authors"] == ["Alice", "Bob"]
    assert result["categories"] == ["cs.AI", "cs.CL"]
    assert result["abstract"] == "Testing abstract about language models"


@pytest.mark.asyncio
async def test_upsert_paper_updates(store):
    """Upserting the same paper_id updates existing fields."""
    await store.upsert_paper(SAMPLE_PAPER)

    updated_paper = {**SAMPLE_PAPER, "title": "Updated Title on LLMs"}
    await store.upsert_paper(updated_paper)

    result = await store.get_paper("2401.12345")
    assert result is not None
    assert result["title"] == "Updated Title on LLMs"


@pytest.mark.asyncio
async def test_get_paper_not_found(store):
    """Returns None for a paper ID that does not exist."""
    result = await store.get_paper("9999.99999")
    assert result is None


@pytest.mark.asyncio
async def test_upsert_and_get_embedding(store):
    """Store and retrieve embedding bytes."""
    embedding_data = b"\x00\x01\x02\x03\x04\x05"
    await store.upsert_embedding("2401.12345", "test-model", embedding_data)

    result = await store.get_embedding("2401.12345", "test-model")
    assert result == embedding_data


@pytest.mark.asyncio
async def test_save_digest(store):
    """Save a digest and verify it returns a positive ID."""
    digest_json = '{"summary": "Test digest", "papers": []}'
    digest_id = await store.save_digest(
        topic="language models",
        paper_count=5,
        digest_json=digest_json,
    )
    assert isinstance(digest_id, int)
    assert digest_id > 0


@pytest.mark.asyncio
async def test_search_papers_cached(store):
    """Insert papers then search by keyword in title/abstract."""
    await store.upsert_paper(SAMPLE_PAPER)

    second_paper = {
        "paper_id": "2401.67890",
        "title": "Graph Neural Networks",
        "authors": ["Charlie"],
        "abstract": "Testing GNN architectures",
        "categories": ["cs.LG"],
        "published": "2024-02-20",
        "url": "https://arxiv.org/pdf/2401.67890",
    }
    await store.upsert_paper(second_paper)

    # Search for "language" should match the first paper only
    results = await store.search_papers_cached("language")
    assert len(results) == 1
    assert results[0]["paper_id"] == "2401.12345"

    # Search for "Testing" should match both papers
    results = await store.search_papers_cached("Testing")
    assert len(results) == 2
