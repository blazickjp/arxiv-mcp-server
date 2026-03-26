import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_digest_saves_to_research_memory():
    from research_mcp_server.tools.digest import handle_digest

    mock_papers = [
        {
            "id": "2603.18063",
            "title": "MCP Threat Taxonomy",
            "authors": ["Author A"],
            "abstract": "We identify 38 threat categories.",
            "categories": ["cs.CR"],
            "published": "2026-03-20",
            "url": "https://arxiv.org/abs/2603.18063",
        }
    ]

    with patch("research_mcp_server.tools.digest._raw_arxiv_search", new_callable=AsyncMock) as mock_search, \
         patch("research_mcp_server.tools.digest.arxiv_limiter") as mock_limiter, \
         patch("research_mcp_server.tools.digest.SQLiteStore") as mock_store_cls, \
         patch("research_mcp_server.tools.digest._save_to_research_memory", new_callable=AsyncMock) as mock_save:

        mock_search.return_value = mock_papers
        mock_limiter.wait = AsyncMock()
        mock_store = MagicMock()
        mock_store.save_digest = AsyncMock(return_value=1)
        mock_store_cls.return_value = mock_store

        result = await handle_digest({
            "topic": "MCP Security",
            "time_range_days": 7,
            "include_citation_counts": False,
        })

        assert len(result) == 1
        assert "MCP Threat Taxonomy" in result[0].text
        mock_save.assert_called_once()
