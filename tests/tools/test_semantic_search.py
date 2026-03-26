"""Tests for semantic search tool."""

import json
import pytest
import numpy as np
from unittest.mock import patch, MagicMock, AsyncMock

from arxiv_mcp_server.tools.semantic_search import handle_semantic_search


def _mock_search_pool():
    """Return a mock search pool of papers."""
    return [
        {
            "id": "2401.12345",
            "title": "Deep Learning for NLP",
            "authors": ["Alice"],
            "abstract": "A paper about deep learning applied to natural language processing.",
            "categories": ["cs.CL"],
            "published": "2024-01-15T00:00:00Z",
            "url": "https://arxiv.org/pdf/2401.12345",
        },
        {
            "id": "2401.67890",
            "title": "Computer Vision Transformers",
            "authors": ["Bob"],
            "abstract": "Vision transformers for image classification tasks.",
            "categories": ["cs.CV"],
            "published": "2024-01-10T00:00:00Z",
            "url": "https://arxiv.org/pdf/2401.67890",
        },
    ]


def _make_mock_model():
    """Create a mock SentenceTransformer model."""
    model = MagicMock()
    # Return normalized embeddings for query and papers
    dim = 8
    query_emb = np.random.randn(1, dim).astype(np.float32)
    query_emb /= np.linalg.norm(query_emb)
    paper_embs = np.random.randn(2, dim).astype(np.float32)
    paper_embs /= np.linalg.norm(paper_embs, axis=1, keepdims=True)

    # First call is for uncached papers, second call is for query
    model.encode = MagicMock(side_effect=[paper_embs, query_emb])
    return model


@pytest.mark.asyncio
async def test_semantic_search_with_model():
    """Test semantic search with embedding model loaded successfully."""
    mock_model = _make_mock_model()
    pool = _mock_search_pool()

    mock_store = MagicMock()
    mock_store.get_embedding = AsyncMock(return_value=None)
    mock_store.upsert_embedding = AsyncMock()

    with (
        patch(
            "arxiv_mcp_server.tools.semantic_search._raw_arxiv_search",
            new_callable=AsyncMock,
            return_value=pool,
        ),
        patch(
            "arxiv_mcp_server.tools.semantic_search._load_model",
            return_value=mock_model,
        ),
        patch(
            "arxiv_mcp_server.tools.semantic_search.SQLiteStore",
            return_value=mock_store,
        ),
    ):
        result = await handle_semantic_search(
            {"query": "natural language processing", "max_results": 2}
        )

    assert len(result) == 1
    content = json.loads(result[0].text)
    assert content["total_results"] <= 2
    assert content["model"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert len(content["papers"]) > 0
    # Each paper should have a semantic_similarity score
    for paper in content["papers"]:
        assert "semantic_similarity" in paper
        assert isinstance(paper["semantic_similarity"], float)


@pytest.mark.asyncio
async def test_semantic_search_fallback():
    """Test semantic search falls back to keyword results when model is unavailable."""
    pool = _mock_search_pool()

    with (
        patch(
            "arxiv_mcp_server.tools.semantic_search._raw_arxiv_search",
            new_callable=AsyncMock,
            return_value=pool,
        ),
        patch(
            "arxiv_mcp_server.tools.semantic_search._load_model",
            return_value=None,
        ),
    ):
        result = await handle_semantic_search(
            {"query": "deep learning", "max_results": 2}
        )

    content = json.loads(result[0].text)
    assert "note" in content
    assert "Embedding model could not be loaded" in content["note"]
    assert content["total_results"] == 2
    assert len(content["papers"]) == 2


@pytest.mark.asyncio
async def test_semantic_search_no_results():
    """Test semantic search with empty search pool."""
    with patch(
        "arxiv_mcp_server.tools.semantic_search._raw_arxiv_search",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await handle_semantic_search(
            {"query": "nonexistent topic xyz", "max_results": 5}
        )

    content = json.loads(result[0].text)
    assert content["total_results"] == 0
    assert content["papers"] == []
    assert "No papers found" in content["note"]
