"""Tests for shared arXiv throttling."""

import pytest

from arxiv_mcp_server.tools import arxiv_rate_limit


@pytest.fixture(autouse=True)
def reset_rate_limiter_state():
    arxiv_rate_limit._last_request_time = 0.0
    arxiv_rate_limit._cooldown_until = 0.0
    yield
    arxiv_rate_limit._last_request_time = 0.0
    arxiv_rate_limit._cooldown_until = 0.0


@pytest.mark.asyncio
async def test_run_sync_with_arxiv_slot_enters_cooldown_on_rate_limit(mocker):
    sleep_mock = mocker.patch.object(arxiv_rate_limit.asyncio, "sleep", autospec=True)

    def raises_429():
        raise RuntimeError("HTTP 429")

    with pytest.raises(RuntimeError, match="HTTP 429"):
        await arxiv_rate_limit.run_sync_with_arxiv_slot(raises_429)

    assert sleep_mock.await_count == 0
    assert arxiv_rate_limit._cooldown_until > 0


@pytest.mark.asyncio
async def test_cooldown_prevents_next_request(mocker):
    arxiv_rate_limit._cooldown_until = arxiv_rate_limit.time.monotonic() + 600
    func = mocker.Mock(return_value="should not run")

    with pytest.raises(arxiv_rate_limit.ArxivCooldownError, match="cooling down"):
        await arxiv_rate_limit.run_sync_with_arxiv_slot(func)

    func.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limited_get_enters_cooldown_on_429(mocker):
    response = mocker.Mock(status_code=429)
    client = mocker.Mock()
    client.get = mocker.AsyncMock(return_value=response)
    mocker.patch.object(arxiv_rate_limit.asyncio, "sleep", autospec=True)

    with pytest.raises(RuntimeError, match="HTTP 429"):
        await arxiv_rate_limit.rate_limited_get(
            client, "https://export.arxiv.org/api/query"
        )

    assert arxiv_rate_limit._cooldown_until > 0
