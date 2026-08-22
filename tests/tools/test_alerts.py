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

    assert len(response) >= 1
    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert "topic" in payload
    assert isinstance(payload["topic"], dict)
    assert payload["topic"]["topic"] == "multi-agent systems"


@pytest.mark.asyncio
async def test_check_alerts_returns_new_papers(monkeypatch, alerts_test_env):
    """check_alerts should return new papers and update last_checked."""

    async def _mock_raw_search(**kwargs):
        # Mirrors post-#189 _raw_arxiv_search: (papers, total_results)
        return (
            [
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
            ],
            1,
        )

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_raw_search)

    await alerts_module.handle_watch_topic({"topic": "agents"})
    response = await alerts_module.handle_check_alerts({})

    assert len(response) >= 1
    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["checked_topics"] == 1
    assert "alerts" in payload
    assert len(payload["alerts"]) >= 1
    assert "new_paper_count" in payload["alerts"][0]
    assert payload["alerts"][0]["new_paper_count"] == 1


@pytest.mark.asyncio
async def test_check_alerts_unpacks_raw_search_tuple(monkeypatch, alerts_test_env):
    """Regression #193: _raw_arxiv_search returns (papers, total); must unpack."""

    async def _mock_tuple(**kwargs):
        return (
            [
                {
                    "id": "2501.00099",
                    "title": "Tuple Paper",
                    "authors": ["B"],
                    "abstract": "y",
                    "categories": ["cs.LG"],
                    "published": "2025-02-01T00:00:00Z",
                    "url": "https://arxiv.org/pdf/2501.00099",
                    "resource_uri": "arxiv://2501.00099",
                }
            ],
            42,
        )

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_tuple)
    await alerts_module.handle_watch_topic({"topic": "moe"})
    response = await alerts_module.handle_check_alerts({})
    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert payload["alerts"][0]["new_paper_count"] == 1
    assert payload["alerts"][0]["new_papers"][0]["id"] == "2501.00099"


@pytest.mark.asyncio
async def test_check_alerts_handles_partial_paper_fields(monkeypatch, alerts_test_env):
    """check_alerts must not raise KeyError when a paper entry is missing optional fields."""

    async def _mock_partial(**kwargs):
        return (
            [
                {
                    "id": "2501.00002",
                    "title": "Sparse Paper",
                    # "authors", "abstract", "url", "resource_uri" intentionally absent
                    "categories": ["cs.AI"],
                    "published": "2025-01-01T00:00:00Z",
                }
            ],
            1,
        )

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_partial)

    await alerts_module.handle_watch_topic({"topic": "agents"})
    response = await alerts_module.handle_check_alerts({})

    assert len(response) >= 1
    payload = json.loads(response[0].text)
    assert "status" in payload


@pytest.mark.asyncio
async def test_list_watches_returns_watch_without_changing_last_checked(
    alerts_test_env,
):
    """list_watches should return saved watches and leave last_checked unchanged."""
    await alerts_module.handle_watch_topic(
        {"topic": "mixture of experts", "categories": ["cs.LG"]}
    )

    first = json.loads((await alerts_module.handle_list_watches({}))[0].text)
    assert first["status"] == "success"
    assert first["watch_count"] == 1
    watch = first["watches"][0]
    assert watch["topic"] == "mixture of experts"
    assert watch["categories"] == ["cs.LG"]
    assert "last_checked" in watch
    last_checked = watch["last_checked"]

    second = json.loads((await alerts_module.handle_list_watches({}))[0].text)
    assert second["watches"][0]["last_checked"] == last_checked
    assert second["watches"][0]["updated_at"] == watch["updated_at"]


@pytest.mark.asyncio
async def test_list_watches_does_not_write_storage(monkeypatch, alerts_test_env):
    """list_watches is read-only and must not persist watches."""
    await alerts_module.handle_watch_topic({"topic": "agents"})
    called = {"count": 0}

    def _fail_save(_payload):
        called["count"] += 1
        raise AssertionError("list_watches must not call _save_watches")

    monkeypatch.setattr(alerts_module, "_save_watches", _fail_save)
    response = await alerts_module.handle_list_watches({})
    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_unwatch_topic_removes_watch_and_errors_when_missing(alerts_test_env):
    """unwatch_topic should delete an exact match and return a clean not-found error."""
    await alerts_module.handle_watch_topic({"topic": "mixture of experts"})

    removed = json.loads(
        (await alerts_module.handle_unwatch_topic({"topic": "mixture of experts"}))[
            0
        ].text
    )
    assert removed["status"] == "success"
    assert removed["topic"] == "mixture of experts"

    listed = json.loads((await alerts_module.handle_list_watches({}))[0].text)
    assert listed["watch_count"] == 0
    assert listed["watches"] == []

    missing = json.loads(
        (await alerts_module.handle_unwatch_topic({"topic": "mixture of experts"}))[
            0
        ].text
    )
    assert missing["status"] == "error"
    assert "not found" in missing["message"].lower()


def test_list_and_unwatch_tool_annotations():
    """list_watches is read-only; unwatch_topic is a write."""
    assert alerts_module.list_watches_tool.annotations.readOnlyHint is True
    assert alerts_module.unwatch_topic_tool.annotations.readOnlyHint is False


def test_check_alerts_tool_annotations_not_readonly():
    """check_alerts mutates last_checked, so it must not be marked read-only."""
    ann = alerts_module.check_alerts_tool.annotations
    assert ann.readOnlyHint is False
    assert ann.idempotentHint is False
