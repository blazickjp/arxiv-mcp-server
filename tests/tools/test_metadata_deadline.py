"""Tests for metadata deadline enforcement (PR #285 P1 fix)."""

import time
from unittest.mock import Mock, patch, MagicMock
import pytest
import requests.exceptions
from arxiv_mcp_server.tools.download import _fetch_arxiv_metadata


class FakeClock:
    """Fake clock for deadline testing without real sleeps."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeResponse:
    """Fake streaming response that advances clock per chunk."""

    def __init__(self, chunks, clock, chunk_delay=0.0, status_code=200):
        self.chunks = chunks
        self.clock = clock
        self.chunk_delay = chunk_delay
        self.status_code = status_code
        self.closed = False

    def iter_content(self, chunk_size=None, decode_unicode=False):
        for chunk in self.chunks:
            if self.chunk_delay:
                self.clock.advance(self.chunk_delay)
            yield chunk

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True


def test_metadata_deadline_skips_when_insufficient_budget_before_gate():
    """Pre-gate check skips metadata lookup when budget insufficient."""
    clock = FakeClock()
    deadline = clock() + 2.0  # Only 2s budget

    with patch("time.monotonic", clock):
        with patch(
            "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
        ) as mock_limiter:
            # pending_wait=3s (typical), min_attempt_time=1s → need 4s, have 2s → skip
            mock_limiter.seconds_until_next_slot.return_value = 3.0

            result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    assert result is None
    # Should not have called run_sync at all
    mock_limiter.run_sync.assert_not_called()


def test_metadata_trickle_past_deadline_returns_none():
    """Slow trickle past deadline closes response and returns None."""
    clock = FakeClock()
    deadline = clock() + 7.0  # 7s budget

    # Feed that would parse successfully
    feed_xml = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Test</title><summary>Test summary</summary></entry></feed>'
    # Split into chunks that arrive every 0.75s
    chunks = [feed_xml[i : i + 10] for i in range(0, len(feed_xml), 10)]

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                # Advance clock 3s for gate wait
                def run_sync_with_delay(operation, timeout=None):
                    clock.advance(3.0)
                    return operation()

                mock_limiter.run_sync.side_effect = run_sync_with_delay
                mock_limiter.seconds_until_next_slot.return_value = 0.1

                # Fake response with 0.75s per chunk
                fake_response = FakeResponse(
                    chunks, clock, chunk_delay=0.75, status_code=200
                )
                mock_get.return_value = fake_response

                result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    # Should return None (trickle exceeded deadline)
    assert result is None
    # Response should be closed
    assert fake_response.closed


def test_metadata_http_406_returns_none_immediately():
    """HTTP 406 returns None with exactly 1 request (issue #277: no retry)."""
    clock = FakeClock()

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
                mock_limiter.seconds_until_next_slot.return_value = 0.0

                fake_response = FakeResponse([], clock, status_code=406)
                mock_get.return_value = fake_response

                result = _fetch_arxiv_metadata("1234.5678", deadline=None)

    # Should return None on 406
    assert result is None
    # Exactly 1 request (no retry)
    assert mock_get.call_count == 1
    # Response should be closed
    assert fake_response.closed


def test_metadata_happy_path_parses_correctly():
    """Successful metadata lookup parses feed and returns correct dict."""
    clock = FakeClock()

    feed_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/1234.5678v1</id>
  <title>Test Paper Title</title>
  <summary>Test paper summary.</summary>
  <published>2024-01-01T00:00:00Z</published>
  <updated>2024-01-02T00:00:00Z</updated>
  <author><name>Test Author</name></author>
  <arxiv:primary_category term="cs.LG"/>
  <category term="cs.AI"/>
  <category term="cs.LG"/>
</entry>
</feed>"""

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
                mock_limiter.seconds_until_next_slot.return_value = 0.0

                fake_response = FakeResponse([feed_xml], clock, status_code=200)
                mock_get.return_value = fake_response

                result = _fetch_arxiv_metadata("1234.5678", deadline=None)

    # Should parse successfully
    assert result is not None
    assert result["title"] == "Test Paper Title"
    assert result["summary"] == "Test paper summary."
    assert "Test Author" in result["authors"]
    assert result["primary_category"] == "cs.LG"
    assert "cs.AI" in result["categories"]
    assert "cs.LG" in result["categories"]


def test_metadata_gate_recheck_skips_after_long_wait():
    """Post-gate recheck skips request if deadline exceeded during gate wait."""
    clock = FakeClock()
    deadline = clock() + 7.0  # 7s budget

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                # Simulate gate wait consuming most of budget
                def run_sync_with_long_wait(operation, timeout=None):
                    clock.advance(6.5)  # Leave only 0.5s
                    return operation()

                mock_limiter.run_sync.side_effect = run_sync_with_long_wait
                mock_limiter.seconds_until_next_slot.return_value = 0.1

                result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    # Should skip (post-gate check fails)
    assert result is None
    # Should not have made request
    mock_get.assert_not_called()


def test_metadata_timeout_on_connect():
    """Connect timeout returns None gracefully."""
    clock = FakeClock()

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
                mock_limiter.seconds_until_next_slot.return_value = 0.0

                mock_get.side_effect = requests.exceptions.ConnectTimeout("Timeout")

                result = _fetch_arxiv_metadata("1234.5678", deadline=None)

    # Should return None on timeout
    assert result is None


def test_metadata_mutation_check_without_per_chunk_deadline():
    """Mutation: without per-chunk deadline check, slow trickle exceeds budget."""
    # This documents what happens WITHOUT the per-chunk deadline check.
    # With chunks arriving every 0.75s and read_timeout > 0.75s, the request
    # completes successfully but takes longer than the budget.
    #
    # The fix checks time.monotonic() against deadline after every chunk and
    # closes the response on overrun, ensuring we stay within budget.
    clock = FakeClock()
    deadline = clock() + 7.0

    feed_xml = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Test</title><summary>Test summary</summary></entry></feed>'
    chunks = [feed_xml[i : i + 10] for i in range(0, len(feed_xml), 10)]

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                # Advance 3s for gate wait
                def run_sync_with_delay(operation, timeout=None):
                    clock.advance(3.0)
                    return operation()

                mock_limiter.run_sync.side_effect = run_sync_with_delay
                mock_limiter.seconds_until_next_slot.return_value = 0.1

                # Chunks every 0.75s would take ~7.5s total (exceeds 7s budget)
                fake_response = FakeResponse(
                    chunks, clock, chunk_delay=0.75, status_code=200
                )
                mock_get.return_value = fake_response

                # With the fix, this returns None (deadline exceeded during streaming)
                result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    # WITH fix: returns None, response closed before completion
    assert result is None
    assert fake_response.closed

    # WITHOUT fix (if we removed the per-chunk check), it would complete
    # successfully but after ~7.5s, exceeding the 7s budget. The mutation
    # test in CI verifies that removing the per-chunk check causes this test
    # to fail (result would be not None).


def test_metadata_parses_arxiv_version_from_entry_id():
    """arxiv_version is parsed from entry id for downgrade protection (#206)."""
    clock = FakeClock()

    feed_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/2404.19756v5</id>
  <title>Test Paper</title>
  <summary>Test summary</summary>
  <published>2024-04-30T00:00:00Z</published>
  <author><name>Test Author</name></author>
  <arxiv:primary_category term="cs.LG"/>
</entry>
</feed>"""

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
                mock_limiter.seconds_until_next_slot.return_value = 0.0

                fake_response = FakeResponse([feed_xml], clock, status_code=200)
                mock_get.return_value = fake_response

                result = _fetch_arxiv_metadata("2404.19756", deadline=None)

    assert result is not None
    assert result["arxiv_version"] == "v5", "Should parse version from entry id"
    assert result["arxiv_url"] == "http://arxiv.org/abs/2404.19756v5"


def test_metadata_collapses_whitespace_in_title():
    """Title whitespace is collapsed to match _metadata_from_arxiv_result."""
    clock = FakeClock()

    # Title with newlines and multiple spaces
    feed_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/1234.5678v1</id>
  <title>Test   Paper
  with   wrapped
    title</title>
  <summary>Test   summary
  with   newlines</summary>
  <published>2024-01-01T00:00:00Z</published>
  <author><name>  Test   Author
  </name></author>
  <author><name>Second Author</name></author>
  <arxiv:primary_category term="cs.LG"/>
</entry>
</feed>"""

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
                mock_limiter.seconds_until_next_slot.return_value = 0.0

                fake_response = FakeResponse([feed_xml], clock, status_code=200)
                mock_get.return_value = fake_response

                result = _fetch_arxiv_metadata("1234.5678", deadline=None)

    assert result is not None
    # Whitespace should be collapsed
    assert result["title"] == "Test Paper with wrapped title"
    assert result["summary"] == "Test summary with newlines"
    # Author names should also be collapsed
    assert "Test Author" in result["authors"]
    assert "Second Author" in result["authors"]


def test_metadata_formats_dates_with_timezone_offset():
    """Published/updated dates use +00:00 format (not Z) for consistency."""
    clock = FakeClock()

    feed_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/1234.5678v1</id>
  <title>Test</title>
  <summary>Test</summary>
  <published>2024-04-30T00:00:00Z</published>
  <updated>2024-05-01T00:00:00Z</updated>
  <author><name>Test</name></author>
  <arxiv:primary_category term="cs.LG"/>
</entry>
</feed>"""

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
                mock_limiter.seconds_until_next_slot.return_value = 0.0

                fake_response = FakeResponse([feed_xml], clock, status_code=200)
                mock_get.return_value = fake_response

                result = _fetch_arxiv_metadata("1234.5678", deadline=None)

    assert result is not None
    # Should use +00:00 format (consistent with _metadata_from_arxiv_result)
    assert result["published"] == "2024-04-30T00:00:00+00:00"
    assert result["updated"] == "2024-05-01T00:00:00+00:00"


def test_metadata_sends_user_agent_header():
    """Requests include User-Agent header as requested by arXiv."""
    clock = FakeClock()

    feed_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/1234.5678v1</id>
  <title>Test</title>
  <summary>Test</summary>
  <published>2024-01-01T00:00:00Z</published>
  <author><name>Test</name></author>
  <arxiv:primary_category term="cs.LG"/>
</entry>
</feed>"""

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
                mock_limiter.seconds_until_next_slot.return_value = 0.0

                fake_response = FakeResponse([feed_xml], clock, status_code=200)
                mock_get.return_value = fake_response

                result = _fetch_arxiv_metadata("1234.5678", deadline=None)

    assert result is not None
    # Check that User-Agent header was sent
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args[1]
    assert "headers" in call_kwargs
    assert "User-Agent" in call_kwargs["headers"]
    assert "arxiv-mcp-server" in call_kwargs["headers"]["User-Agent"]


def test_metadata_watchdog_closes_connection_on_deadline():
    """Watchdog timer closes response at deadline - margin even if chunks arrive slowly."""
    clock = FakeClock()
    deadline = clock() + 7.0

    feed_xml = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Test</title><summary>Test</summary></entry></feed>'

    # Create a response that will be closed by the watchdog
    fake_response = Mock()
    fake_response.status_code = 200
    fake_response.closed = False

    def raise_for_status():
        if fake_response.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {fake_response.status_code}")

    fake_response.raise_for_status = raise_for_status

    # Mock iter_content to never yield (simulating a stall)
    # The watchdog should fire and close the response
    def iter_content_stall(chunk_size=None, decode_unicode=False):
        # Advance clock past deadline to simulate slow trickle
        clock.advance(10.0)
        # This should never be reached because watchdog fires
        yield feed_xml

    fake_response.iter_content = iter_content_stall
    fake_response.close = Mock()

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                mock_limiter.run_sync.side_effect = lambda op, timeout=None: op()
                mock_limiter.seconds_until_next_slot.return_value = 0.0

                # Use a real threading.Timer for the watchdog
                with patch(
                    "threading.Timer", wraps=__import__("threading").Timer
                ) as mock_timer:
                    mock_get.return_value = fake_response

                    result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    # The watchdog should have been created
    assert mock_timer.called, "Watchdog timer should be created when deadline is set"
    # Response should be closed (either by watchdog or finally block)
    assert fake_response.close.called, "Response should be closed"


def test_metadata_gate_timeout_returns_none_without_request():
    """Gate timeout prevents metadata lookup when gate wait would exceed budget."""
    clock = FakeClock()
    deadline = clock() + 7.0  # 7s budget

    feed_xml = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/1234.5678v1</id>
  <title>Test</title>
  <summary>Test</summary>
  <published>2024-01-01T00:00:00Z</published>
  <author><name>Test</name></author>
  <arxiv:primary_category term="cs.LG"/>
</entry>
</feed>"""

    with patch("time.monotonic", clock):
        with patch("requests.get") as mock_get:
            with patch(
                "arxiv_mcp_server.tools.download.ARXIV_RATE_LIMITER"
            ) as mock_limiter:
                # Simulate gate timeout - raise GateTimeout without calling operation
                def run_sync_with_timeout(operation, timeout=None):
                    if timeout is not None:
                        # Gate acquisition times out
                        from arxiv_mcp_server.arxiv_api import GateTimeout

                        raise GateTimeout(
                            f"Could not acquire rate limiter gate within {timeout}s timeout"
                        )
                    return operation()

                mock_limiter.run_sync.side_effect = run_sync_with_timeout
                mock_limiter.seconds_until_next_slot.return_value = (
                    5.0  # 5s pending wait
                )

                fake_response = FakeResponse([feed_xml], clock, status_code=200)
                mock_get.return_value = fake_response

                result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    # Should return None (gate timeout)
    assert result is None
    # Should make 0 requests (operation never called, preserving rate limiting)
    assert mock_get.call_count == 0
