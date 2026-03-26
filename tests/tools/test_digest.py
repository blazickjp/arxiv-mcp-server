"""Tests for research digest tool."""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from research_mcp_server.tools.digest import handle_digest
from research_mcp_server.clients.s2_client import S2Client


def _mock_papers():
    """Return mock papers for digest generation."""
    return [
        {
            "id": "2401.00001",
            "title": "Deep Reinforcement Learning Survey",
            "authors": ["Alice", "Bob"],
            "abstract": "A comprehensive survey of deep reinforcement learning methods and applications.",
            "categories": ["cs.AI", "cs.LG"],
            "published": "2024-01-10T00:00:00Z",
            "url": "https://arxiv.org/pdf/2401.00001",
        },
        {
            "id": "2401.00002",
            "title": "Policy Gradient Methods",
            "authors": ["Charlie"],
            "abstract": "Novel policy gradient methods for continuous control.",
            "categories": ["cs.AI"],
            "published": "2024-01-12T00:00:00Z",
            "url": "https://arxiv.org/pdf/2401.00002",
        },
    ]


@pytest.mark.asyncio
async def test_research_digest():
    """Test digest generation with search and S2 citation data."""
    papers = _mock_papers()

    s2_batch_result = [
        {
            "paperId": "s2_aaa",
            "externalIds": {"ArXiv": "2401.00001"},
            "citationCount": 25,
            "influentialCitationCount": 5,
        },
        {
            "paperId": "s2_bbb",
            "externalIds": {"ArXiv": "2401.00002"},
            "citationCount": 10,
            "influentialCitationCount": 2,
        },
    ]

    mock_store = MagicMock()
    mock_store.save_digest = AsyncMock(return_value=1)

    with (
        patch(
            "research_mcp_server.tools.digest._raw_arxiv_search",
            new_callable=AsyncMock,
            return_value=papers,
        ),
        patch(
            "research_mcp_server.tools.digest.arxiv_limiter",
        ) as mock_limiter,
        patch.object(
            S2Client,
            "batch_get_papers",
            new_callable=AsyncMock,
            return_value=s2_batch_result,
        ),
        patch(
            "research_mcp_server.tools.digest.SQLiteStore",
            return_value=mock_store,
        ),
    ):
        mock_limiter.wait = AsyncMock()

        result = await handle_digest(
            {
                "topic": "reinforcement learning",
                "time_range_days": 30,
                "max_papers": 20,
                "include_citation_counts": True,
            }
        )

    assert len(result) == 1
    text = result[0].text

    # The response contains markdown followed by raw JSON
    assert "# Research Digest" in text
    assert "reinforcement learning" in text.lower() or "Reinforcement" in text

    # Extract JSON from the markdown code block
    json_start = text.find("```json\n") + len("```json\n")
    json_end = text.find("\n```", json_start)
    digest_json = json.loads(text[json_start:json_end])

    assert "digest_metadata" in digest_json
    assert digest_json["digest_metadata"]["topic"] == "reinforcement learning"
    assert digest_json["digest_metadata"]["total_papers"] == 2

    assert len(digest_json["highlights"]) > 0
    assert len(digest_json["papers"]) == 2
    assert len(digest_json["themes"]) > 0
    assert "stats" in digest_json


@pytest.mark.asyncio
async def test_digest_without_citations():
    """Test digest generation with include_citation_counts=false skips S2 lookup."""
    papers = _mock_papers()

    mock_store = MagicMock()
    mock_store.save_digest = AsyncMock(return_value=1)

    with (
        patch(
            "research_mcp_server.tools.digest._raw_arxiv_search",
            new_callable=AsyncMock,
            return_value=papers,
        ),
        patch(
            "research_mcp_server.tools.digest.arxiv_limiter",
        ) as mock_limiter,
        patch.object(
            S2Client,
            "batch_get_papers",
            new_callable=AsyncMock,
        ) as mock_s2_batch,
        patch(
            "research_mcp_server.tools.digest.SQLiteStore",
            return_value=mock_store,
        ),
    ):
        mock_limiter.wait = AsyncMock()

        result = await handle_digest(
            {
                "topic": "reinforcement learning",
                "include_citation_counts": False,
            }
        )

    # S2 batch should NOT have been called
    mock_s2_batch.assert_not_called()

    text = result[0].text
    assert "# Research Digest" in text

    json_start = text.find("```json\n") + len("```json\n")
    json_end = text.find("\n```", json_start)
    digest_json = json.loads(text[json_start:json_end])

    assert digest_json["digest_metadata"]["total_papers"] == 2
    # Papers should not have citation_count set
    for paper in digest_json["papers"]:
        assert paper.get("citation_count") is None
