"""Tests for research alert tools."""

import json
from pathlib import Path

import pytest

from arxiv_mcp_server.tools import alerts as alerts_module


@pytest.fixture
def alerts_test_env(monkeypatch, temp_storage_path):
    """Configure alerts module to use temporary storage."""
    monkeypatch.setattr(
        alerts_module.settings,
        "_get_storage_path_from_args",
        lambda: Path(temp_storage_path),
    )


@pytest.mark.asyncio
async def test_watch_topic_persists_topic(alerts_test_env):
    """watch_topic should persist watched topic payloads."""
    response = await alerts_module.handle_watch_topic(
        {"topic": "multi-agent systems", "categories": ["cs.AI"]}
    )

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["topic"]["topic"] == "multi-agent systems"


@pytest.mark.asyncio
async def test_check_alerts_returns_new_papers(monkeypatch, alerts_test_env):
    """check_alerts should return new papers and update last_checked."""

    async def _mock_raw_search(**kwargs):
        return [
            {
                "id": "2501.00001",
                "title": "New Paper",
                "authors": ["A"],
                "abstract": "x",
                "categories": ["cs.AI"],
                "published": "2025-01-01T00:00:00Z",
                "url": "https://arxiv.org/pdf/2501.00001",
                "resource_uri": "arxiv://2501.00001",
            }
        ]

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_raw_search)

    await alerts_module.handle_watch_topic({"topic": "agents"})
    response = await alerts_module.handle_check_alerts({})

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["checked_topics"] == 1
    assert payload["alerts"][0]["new_paper_count"] == 1
