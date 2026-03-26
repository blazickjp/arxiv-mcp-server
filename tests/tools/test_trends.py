"""Tests for trend analysis tool."""

import json
import pytest
from unittest.mock import patch, AsyncMock

from arxiv_mcp_server.tools.trends import handle_trend_analysis


def _mock_papers_with_dates():
    """Return mock papers spanning multiple months."""
    return [
        {
            "id": "2401.00001",
            "title": "Transformer Architecture Advances",
            "authors": ["Alice", "Bob"],
            "abstract": "Advances in transformer architecture.",
            "categories": ["cs.AI"],
            "published": "2024-01-10T00:00:00Z",
            "url": "https://arxiv.org/pdf/2401.00001",
        },
        {
            "id": "2401.00002",
            "title": "Transformer Training Optimization",
            "authors": ["Alice"],
            "abstract": "Optimizing transformer training.",
            "categories": ["cs.LG"],
            "published": "2024-01-20T00:00:00Z",
            "url": "https://arxiv.org/pdf/2401.00002",
        },
        {
            "id": "2402.00001",
            "title": "Large Language Model Scaling",
            "authors": ["Charlie"],
            "abstract": "Scaling laws for language models.",
            "categories": ["cs.CL"],
            "published": "2024-02-05T00:00:00Z",
            "url": "https://arxiv.org/pdf/2402.00001",
        },
        {
            "id": "2403.00001",
            "title": "Vision Transformer Benchmarks",
            "authors": ["Bob"],
            "abstract": "Benchmarking vision transformers.",
            "categories": ["cs.CV"],
            "published": "2024-03-01T00:00:00Z",
            "url": "https://arxiv.org/pdf/2403.00001",
        },
    ]


@pytest.mark.asyncio
async def test_trend_analysis_monthly():
    """Test trend analysis with monthly granularity has monthly keys."""
    papers = _mock_papers_with_dates()

    with (
        patch(
            "arxiv_mcp_server.tools.trends._raw_arxiv_search",
            new_callable=AsyncMock,
            return_value=papers,
        ),
        patch(
            "arxiv_mcp_server.tools.trends.S2Client",
        ) as mock_s2_cls,
    ):
        mock_s2 = mock_s2_cls.return_value
        mock_s2.batch_get_papers = AsyncMock(return_value=[])

        result = await handle_trend_analysis(
            {"topic": "transformers", "granularity": "monthly"}
        )

    assert len(result) == 1
    content = json.loads(result[0].text)

    assert content["topic"] == "transformers"
    assert content["granularity"] == "monthly"
    assert content["total_papers"] == 4

    volume = content["volume_over_time"]
    # Should have monthly keys like "2024-01", "2024-02", "2024-03"
    assert len(volume) >= 3
    for key in volume:
        # Monthly format: YYYY-MM
        assert len(key) == 7
        assert key[4] == "-"

    assert len(content["top_keywords"]) > 0
    assert len(content["top_authors"]) > 0
    assert len(content["top_papers"]) <= 10


@pytest.mark.asyncio
async def test_trend_analysis_empty():
    """Test trend analysis when no papers are found."""
    with patch(
        "arxiv_mcp_server.tools.trends._raw_arxiv_search",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await handle_trend_analysis({"topic": "nonexistent topic xyz"})

    content = json.loads(result[0].text)
    assert content["total_papers"] == 0
    assert "No papers found" in content["note"]
