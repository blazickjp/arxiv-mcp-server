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
    # #227: new watches seed last_checked to creation time (not null).
    assert payload["topic"]["last_checked"] is not None
    assert payload["topic"]["last_checked"] == payload["topic"]["created_at"]


@pytest.mark.asyncio
async def test_watch_topic_update_omitted_categories_preserved(alerts_test_env):
    """Regression #222: update with only max_results must keep categories."""
    create = await alerts_module.handle_watch_topic(
        {
            "topic": "wipe-demo",
            "categories": ["cs.LG", "cs.AI"],
            "max_results": 3,
        }
    )
    created = json.loads(create[0].text)["topic"]
    assert created["categories"] == ["cs.LG", "cs.AI"]
    assert created["max_results"] == 3

    # Simulate a prior check so last_checked is set and must be preserved.
    payload = alerts_module._load_watches()
    payload["topics"][0]["last_checked"] = "2024-06-01T00:00:00+00:00"
    alerts_module._save_watches(payload)

    update = await alerts_module.handle_watch_topic(
        {"topic": "wipe-demo", "max_results": 7}
    )
    updated = json.loads(update[0].text)["topic"]
    assert updated["categories"] == ["cs.LG", "cs.AI"]
    assert updated["max_results"] == 7
    assert updated["last_checked"] == "2024-06-01T00:00:00+00:00"

    stored = alerts_module._load_watches()["topics"][0]
    assert stored["categories"] == ["cs.LG", "cs.AI"]
    assert stored["max_results"] == 7
    assert stored["last_checked"] == "2024-06-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_watch_topic_update_explicit_empty_categories_clears(alerts_test_env):
    """Regression #222: explicit categories=[] still clears on update."""
    await alerts_module.handle_watch_topic(
        {
            "topic": "wipe-demo",
            "categories": ["cs.LG", "cs.AI"],
            "max_results": 3,
        }
    )
    update = await alerts_module.handle_watch_topic(
        {"topic": "wipe-demo", "categories": [], "max_results": 7}
    )
    updated = json.loads(update[0].text)["topic"]
    assert updated["categories"] == []
    assert updated["max_results"] == 7

    stored = alerts_module._load_watches()["topics"][0]
    assert stored["categories"] == []


@pytest.mark.asyncio
async def test_watch_topic_update_explicit_categories_replaces(alerts_test_env):
    """Regression #222: passing a new categories list replaces stored filters."""
    await alerts_module.handle_watch_topic(
        {"topic": "wipe-demo", "categories": ["cs.LG"], "max_results": 3}
    )
    update = await alerts_module.handle_watch_topic(
        {"topic": "wipe-demo", "categories": ["cs.AI", "stat.ML"]}
    )
    updated = json.loads(update[0].text)["topic"]
    assert updated["categories"] == ["cs.AI", "stat.ML"]

    stored = alerts_module._load_watches()["topics"][0]
    assert stored["categories"] == ["cs.AI", "stat.ML"]


@pytest.mark.asyncio
async def test_watch_topic_create_without_categories_defaults_empty(alerts_test_env):
    """Create path unchanged: omitted categories still defaults to []."""
    response = await alerts_module.handle_watch_topic({"topic": "fresh-topic"})
    payload = json.loads(response[0].text)
    assert payload["topic"]["categories"] == []
    stored = alerts_module._load_watches()["topics"][0]
    assert stored["categories"] == []


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
    # Established-watch baseline: last_checked in the past so mocks are "new".
    _seed = alerts_module._load_watches()
    _seed["topics"][0]["last_checked"] = "2024-01-01T00:00:00+00:00"
    alerts_module._save_watches(_seed)
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
    # Established-watch baseline: last_checked in the past so mocks are "new".
    _seed = alerts_module._load_watches()
    _seed["topics"][0]["last_checked"] = "2024-01-01T00:00:00+00:00"
    alerts_module._save_watches(_seed)
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


@pytest.mark.asyncio
async def test_check_alerts_unknown_topic_returns_not_found(
    monkeypatch, alerts_test_env
):
    """Regression #221: unknown topic must error, not silent empty success."""

    async def _fail_search(**kwargs):
        raise AssertionError("check_alerts must not search when watch is missing")

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _fail_search)

    await alerts_module.handle_watch_topic({"topic": "real"})

    missing = json.loads(
        (await alerts_module.handle_check_alerts({"topic": "definitely-missing"}))[
            0
        ].text
    )
    assert missing["status"] == "error"
    assert "not found" in missing["message"].lower()
    assert "definitely-missing" in missing["message"]
    assert "checked_topics" not in missing
    assert "alerts" not in missing

    # Existing watch must still be intact (no save / mutate on not-found).
    listed = json.loads((await alerts_module.handle_list_watches({}))[0].text)
    assert listed["watch_count"] == 1
    assert listed["watches"][0]["topic"] == "real"


@pytest.mark.asyncio
async def test_check_alerts_omit_topic_empty_watches_still_success(alerts_test_env):
    """Omitting topic with no watches remains empty success (check-all unchanged)."""
    result = json.loads((await alerts_module.handle_check_alerts({}))[0].text)
    assert result["status"] == "success"
    assert result["checked_topics"] == 0
    assert result["alerts"] == []


@pytest.mark.asyncio
async def test_check_alerts_known_topic_still_succeeds(monkeypatch, alerts_test_env):
    """A matching topic still returns success with alerts (possibly empty)."""

    async def _mock_empty(**kwargs):
        return ([], 0)

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_empty)

    await alerts_module.handle_watch_topic({"topic": "real", "max_results": 5})
    result = json.loads(
        (await alerts_module.handle_check_alerts({"topic": "real"}))[0].text
    )
    assert result["status"] == "success"
    assert result["checked_topics"] == 1
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["topic"] == "real"
    assert result["alerts"][0]["new_paper_count"] == 0


def test_list_and_unwatch_tool_annotations():
    """list_watches is read-only; unwatch_topic is a write."""
    assert alerts_module.list_watches_tool.annotations.readOnlyHint is True
    assert alerts_module.unwatch_topic_tool.annotations.readOnlyHint is False


def test_check_alerts_tool_annotations_not_readonly():
    """check_alerts mutates last_checked, so it must not be marked read-only."""
    ann = alerts_module.check_alerts_tool.annotations
    assert ann.readOnlyHint is False
    assert ann.idempotentHint is False


@pytest.mark.asyncio
async def test_check_alerts_truncated_page_does_not_jump_watermark_to_now(
    monkeypatch, alerts_test_env
):
    """Regression #217: truncated max_results must not advance last_checked to now."""
    calls = {"n": 0}

    async def _mock_truncated(**kwargs):
        calls["n"] += 1
        assert kwargs.get("sort_order") == "ascending"
        # Page full (max_results=1) while OpenSearch reports many more hits.
        return (
            [
                {
                    "id": "2401.00001",
                    "title": "Oldest unread",
                    "authors": ["A"],
                    "abstract": "x",
                    "categories": ["cs.LG"],
                    "published": "2024-06-01T12:00:00+00:00",
                    "url": "https://arxiv.org/pdf/2401.00001",
                    "resource_uri": "arxiv://2401.00001",
                }
            ],
            9321,
        )

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_truncated)

    await alerts_module.handle_watch_topic(
        {"topic": 'ti:"neural" AND cat:cs.LG', "max_results": 1}
    )

    # Seed last_checked far in the past (repro from #217).
    payload = alerts_module._load_watches()
    payload["topics"][0]["last_checked"] = "2024-01-01T00:00:00+00:00"
    alerts_module._save_watches(payload)

    before = "2024-01-01T00:00:00+00:00"
    response = await alerts_module.handle_check_alerts(
        {"topic": 'ti:"neural" AND cat:cs.LG'}
    )
    result = json.loads(response[0].text)
    assert result["status"] == "success"
    alert = result["alerts"][0]
    assert alert["new_paper_count"] == 1
    assert alert["has_more"] is True

    stored = alerts_module._load_watches()["topics"][0]
    assert stored["last_checked"] == "2024-06-01T12:00:00+00:00"
    assert stored["last_checked"] != before
    # Must not have jumped to wall-clock "now".
    assert not stored["last_checked"].startswith("2026-")
    assert "T" in stored["last_checked"]
    from dateutil import parser as date_parser
    from datetime import datetime, timezone, timedelta

    watermark = date_parser.parse(stored["last_checked"])
    assert watermark < datetime.now(timezone.utc) - timedelta(days=30)


@pytest.mark.asyncio
async def test_check_alerts_truncated_page_continues_draining(
    monkeypatch, alerts_test_env
):
    """Regression #217: subsequent checks paginate start until the window is caught up."""
    corpus = [
        {
            "id": "2401.00001",
            "title": "Paper A",
            "authors": ["A"],
            "abstract": "x",
            "categories": ["cs.LG"],
            "published": "2024-02-01T00:00:00+00:00",
            "url": "https://arxiv.org/pdf/2401.00001",
            "resource_uri": "arxiv://2401.00001",
        },
        {
            "id": "2401.00002",
            "title": "Paper B",
            "authors": ["B"],
            "abstract": "y",
            "categories": ["cs.LG"],
            "published": "2024-03-01T00:00:00+00:00",
            "url": "https://arxiv.org/pdf/2401.00002",
            "resource_uri": "arxiv://2401.00002",
        },
        {
            "id": "2401.00003",
            "title": "Paper C",
            "authors": ["C"],
            "abstract": "z",
            "categories": ["cs.LG"],
            "published": "2024-04-01T00:00:00+00:00",
            "url": "https://arxiv.org/pdf/2401.00003",
            "resource_uri": "arxiv://2401.00003",
        },
    ]
    seen = []

    async def _mock_pages(**kwargs):
        start = int(kwargs.get("start") or 0)
        seen.append({"date_from": kwargs.get("date_from"), "start": start})
        page = corpus[start : start + 1]
        return page, len(corpus)

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_pages)

    await alerts_module.handle_watch_topic({"topic": "drain-me", "max_results": 1})
    payload = alerts_module._load_watches()
    payload["topics"][0]["last_checked"] = "2024-01-01T00:00:00+00:00"
    alerts_module._save_watches(payload)

    first = json.loads((await alerts_module.handle_check_alerts({}))[0].text)
    assert first["alerts"][0]["has_more"] is True
    assert first["alerts"][0]["new_papers"][0]["id"] == "2401.00001"
    stored = alerts_module._load_watches()["topics"][0]
    assert stored["last_checked"] == "2024-02-01T00:00:00+00:00"
    assert stored["check_start"] == 1
    assert stored["drain_from"] == "2024-01-01T00:00:00+00:00"

    second = json.loads((await alerts_module.handle_check_alerts({}))[0].text)
    assert second["alerts"][0]["has_more"] is True
    assert second["alerts"][0]["new_papers"][0]["id"] == "2401.00002"
    stored = alerts_module._load_watches()["topics"][0]
    assert stored["last_checked"] == "2024-03-01T00:00:00+00:00"
    assert stored["check_start"] == 2

    # Last paper: start+returned == total => has_more clears, cursor resets to now.
    third = json.loads((await alerts_module.handle_check_alerts({}))[0].text)
    assert third["alerts"][0]["has_more"] is False
    assert third["alerts"][0]["new_papers"][0]["id"] == "2401.00003"
    stored = alerts_module._load_watches()["topics"][0]
    assert stored["last_checked"] != "2024-04-01T00:00:00+00:00"
    assert stored["last_checked"] != "2024-01-01T00:00:00+00:00"
    assert "check_start" not in stored
    assert "drain_from" not in stored
    # date_from stays frozen at the drain window; start paginates.
    assert [s["date_from"] for s in seen] == ["2024-01-01T00:00:00+00:00"] * 3
    assert [s["start"] for s in seen] == [0, 1, 2]


@pytest.mark.asyncio
async def test_check_alerts_boundary_hit_does_not_stick_empty_has_more(
    monkeypatch, alerts_test_env
):
    """Regression #217 live FAIL: Atom re-returns the watermark paper; must still progress.

    After call0 sets last_checked to the returned published time, a naive start=0
    refetch yields the same boundary paper, which `_is_new_paper` drops (strict >).
    Drain must either return a different next paper or clear has_more — never
    empty+has_more with a frozen cursor.
    """
    paper_a = {
        "id": "2401.00633",
        "title": "Boundary",
        "authors": ["A"],
        "abstract": "x",
        "categories": ["cs.LG"],
        "published": "2024-01-01T02:03:35Z",
        "url": "https://arxiv.org/pdf/2401.00633",
        "resource_uri": "arxiv://2401.00633",
    }
    paper_b = {
        "id": "2401.00634",
        "title": "Next",
        "authors": ["B"],
        "abstract": "y",
        "categories": ["cs.LG"],
        "published": "2024-01-01T05:00:00Z",
        "url": "https://arxiv.org/pdf/2401.00634",
        "resource_uri": "arxiv://2401.00634",
    }
    corpus = [paper_a, paper_b]
    seen = []

    async def _mock_atom(**kwargs):
        # Simulate day-granular date_from: always the same corpus, honor start.
        start = int(kwargs.get("start") or 0)
        seen.append({"start": start, "date_from": kwargs.get("date_from")})
        page = corpus[start : start + int(kwargs.get("max_results") or 1)]
        return page, len(corpus)

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_atom)

    await alerts_module.handle_watch_topic(
        {"topic": 'ti:"neural" AND cat:cs.LG', "max_results": 1}
    )
    payload = alerts_module._load_watches()
    payload["topics"][0]["last_checked"] = "2024-01-01T00:00:00+00:00"
    alerts_module._save_watches(payload)

    call0 = json.loads(
        (
            await alerts_module.handle_check_alerts(
                {"topic": 'ti:"neural" AND cat:cs.LG'}
            )
        )[0].text
    )
    assert call0["alerts"][0]["new_paper_count"] == 1
    assert call0["alerts"][0]["new_papers"][0]["id"] == "2401.00633"
    assert call0["alerts"][0]["has_more"] is True
    stored = alerts_module._load_watches()["topics"][0]
    assert stored["last_checked"] == "2024-01-01T02:03:35Z"
    assert not stored["last_checked"].startswith("2026-")
    assert stored["check_start"] == 1

    call1 = json.loads(
        (
            await alerts_module.handle_check_alerts(
                {"topic": 'ti:"neural" AND cat:cs.LG'}
            )
        )[0].text
    )
    alert1 = call1["alerts"][0]
    stored1 = alerts_module._load_watches()["topics"][0]
    # Second call must return the next paper (start=1), not re-trap on boundary.
    assert alert1["new_paper_count"] == 1
    assert alert1["new_papers"][0]["id"] == "2401.00634"
    assert alert1["new_papers"][0]["id"] != "2401.00633"
    # Exhausted total=2 => has_more false and drain cursor cleared.
    assert alert1["has_more"] is False
    assert "check_start" not in stored1
    assert "drain_from" not in stored1
    assert [s["start"] for s in seen] == [0, 1]


@pytest.mark.asyncio
async def test_check_alerts_full_page_marks_caught_up(monkeypatch, alerts_test_env):
    """When fewer than max_results are returned, watermark advances to now safely."""

    async def _mock_partial(**kwargs):
        return (
            [
                {
                    "id": "2501.00010",
                    "title": "Only Paper",
                    "authors": ["A"],
                    "abstract": "x",
                    "categories": ["cs.AI"],
                    "published": "2025-01-15T00:00:00+00:00",
                    "url": "https://arxiv.org/pdf/2501.00010",
                    "resource_uri": "arxiv://2501.00010",
                }
            ],
            1,
        )

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_partial)

    await alerts_module.handle_watch_topic({"topic": "caught-up", "max_results": 10})
    payload = alerts_module._load_watches()
    payload["topics"][0]["last_checked"] = "2025-01-01T00:00:00+00:00"
    alerts_module._save_watches(payload)

    result = json.loads((await alerts_module.handle_check_alerts({}))[0].text)
    assert result["alerts"][0]["has_more"] is False
    assert result["alerts"][0]["new_paper_count"] == 1

    stored = alerts_module._load_watches()["topics"][0]["last_checked"]
    assert stored != "2025-01-01T00:00:00+00:00"
    assert stored != "2025-01-15T00:00:00+00:00"
    from dateutil import parser as date_parser
    from datetime import datetime, timezone, timedelta

    assert date_parser.parse(stored) >= datetime.now(timezone.utc) - timedelta(
        minutes=5
    )


def test_page_is_truncated_helpers():
    """Unit coverage for truncation detection used by check_alerts."""
    assert alerts_module._page_is_truncated(1, 1, 9321) is True
    assert alerts_module._page_is_truncated(10, 10, 10) is False
    assert alerts_module._page_is_truncated(3, 10, 3) is False
    assert alerts_module._page_is_truncated(0, 10, 0) is False
    assert alerts_module._page_is_truncated(5, 10, 12) is True
    # With start offset: last page of a known total is not truncated.
    assert alerts_module._page_is_truncated(1, 1, 3, start=2) is False
    assert alerts_module._page_is_truncated(1, 1, 3, start=1) is True
    # Without total, a full page is assumed truncated.
    assert alerts_module._page_is_truncated(1, 1, None) is True
    assert alerts_module._page_is_truncated(0, 1, None) is False
    assert (
        alerts_module._newest_published(
            [
                {"published": "2024-01-01T00:00:00Z"},
                {"published": "2024-06-01T12:00:00+00:00"},
                {"published": "2024-03-01T00:00:00Z"},
            ]
        )
        == "2024-06-01T12:00:00+00:00"
    )


@pytest.mark.asyncio
async def test_watch_topic_create_seeds_last_checked_to_now(alerts_test_env):
    """Regression #227: new watches must seed last_checked so history is not alerted."""
    from dateutil import parser as date_parser
    from datetime import datetime, timezone, timedelta

    response = await alerts_module.handle_watch_topic(
        {"topic": "mixture-of-experts flood", "categories": ["cs.LG"]}
    )
    topic = json.loads(response[0].text)["topic"]
    assert topic["last_checked"] is not None
    assert topic["last_checked"] == topic["created_at"]
    watermark = date_parser.parse(topic["last_checked"])
    assert watermark >= datetime.now(timezone.utc) - timedelta(minutes=5)

    stored = alerts_module._load_watches()["topics"][0]
    assert stored["last_checked"] == topic["last_checked"]
    assert stored["last_checked"] is not None


@pytest.mark.asyncio
async def test_check_alerts_new_watch_does_not_dump_historical_papers(
    monkeypatch, alerts_test_env
):
    """Regression #227: first check after create returns 0 alerts for old papers."""
    from dateutil import parser as date_parser
    from datetime import datetime, timezone, timedelta

    calls = []

    async def _mock_old_flood(**kwargs):
        calls.append(kwargs)
        # With a seeded watermark, Atom date_from bounds the query — no historical dump.
        # Pre-fix (null last_checked / date_from=None) would return this flood.
        if kwargs.get("date_from"):
            return ([], 0)
        return (
            [
                {
                    "id": "2206.00001",
                    "title": "Ancient MoE",
                    "authors": ["A"],
                    "abstract": "x",
                    "categories": ["cs.LG"],
                    "published": "2022-06-07T00:00:00+00:00",
                    "url": "https://arxiv.org/pdf/2206.00001",
                    "resource_uri": "arxiv://2206.00001",
                },
                {
                    "id": "2301.00002",
                    "title": "Also old",
                    "authors": ["B"],
                    "abstract": "y",
                    "categories": ["cs.LG"],
                    "published": "2023-01-15T00:00:00+00:00",
                    "url": "https://arxiv.org/pdf/2301.00002",
                    "resource_uri": "arxiv://2301.00002",
                },
            ],
            9000,
        )

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_old_flood)

    create = await alerts_module.handle_watch_topic(
        {"topic": 'ti:"mixture of experts"', "max_results": 10}
    )
    created = json.loads(create[0].text)["topic"]
    seeded = created["last_checked"]
    assert seeded is not None

    result = json.loads((await alerts_module.handle_check_alerts({}))[0].text)
    assert result["status"] == "success"
    alert = result["alerts"][0]
    assert alert["new_paper_count"] == 0
    assert alert["new_papers"] == []

    stored = alerts_module._load_watches()["topics"][0]
    # Watermark must not jump into the distant past (the #227 failure mode).
    assert stored["last_checked"] is not None
    watermark = date_parser.parse(stored["last_checked"])
    assert watermark >= datetime.now(timezone.utc) - timedelta(minutes=5)
    assert not stored["last_checked"].startswith("2022-")
    assert not stored["last_checked"].startswith("2023-")
    # date_from passed to search should be the seeded creation watermark.
    assert calls and calls[0].get("date_from") == seeded


@pytest.mark.asyncio
async def test_check_alerts_new_watch_still_surfaces_future_papers(
    monkeypatch, alerts_test_env
):
    """Regression #227: papers published after watch create still alert normally."""
    from datetime import datetime, timezone, timedelta

    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    async def _mock_future(**kwargs):
        return (
            [
                {
                    "id": "2608.00001",
                    "title": "Brand new",
                    "authors": ["A"],
                    "abstract": "x",
                    "categories": ["cs.LG"],
                    "published": future,
                    "url": "https://arxiv.org/pdf/2608.00001",
                    "resource_uri": "arxiv://2608.00001",
                }
            ],
            1,
        )

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _mock_future)

    await alerts_module.handle_watch_topic({"topic": "future-moe", "max_results": 10})
    result = json.loads((await alerts_module.handle_check_alerts({}))[0].text)
    assert result["alerts"][0]["new_paper_count"] == 1
    assert result["alerts"][0]["new_papers"][0]["id"] == "2608.00001"
    assert result["alerts"][0]["has_more"] is False


@pytest.mark.asyncio
async def test_check_alerts_429_returns_rate_limited_json(monkeypatch, alerts_test_env):
    """Regression #255: arXiv 429 must return JSON status=rate_limited, not Error: text."""
    from arxiv_mcp_server.tools.search import ArxivRateLimitError

    async def _raise_rate_limit(**kwargs):
        raise ArxivRateLimitError(
            "arXiv is rate limiting this IP (HTTP 429). Please wait before retrying.",
            status_code=429,
            retry_after_seconds=30.0,
        )

    monkeypatch.setattr(alerts_module, "_raw_arxiv_search", _raise_rate_limit)

    await alerts_module.handle_watch_topic({"topic": "rate-limit-demo"})
    response = await alerts_module.handle_check_alerts({})

    assert len(response) >= 1
    assert not response[0].text.startswith("Error:")
    payload = json.loads(response[0].text)
    assert payload["status"] == "rate_limited"
    assert "HTTP 429" in payload["message"] or "rate limiting" in payload["message"]
    assert payload["http_status"] == 429
    assert payload["retry_after_seconds"] == 30.0
