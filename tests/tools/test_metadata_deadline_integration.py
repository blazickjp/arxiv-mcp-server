"""Integration tests for metadata deadline enforcement with a local slow socket server.

These tests use a real HTTP server with controlled delays to verify that:
1. Socket timeout mechanism enforces deadlines (primary, platform-independent)
2. The watchdog timer provides backstop protection
3. The per-byte deadline check provides additional safeguard
4. All mechanisms fail gracefully and return None within the deadline

Note: With the socket timeout mechanism as primary enforcement, these tests
pass even if watchdog or per-byte check is disabled. See test_metadata_deadline.py
for per-mechanism unit tests that verify each mechanism independently.
"""

import http.server
import socketserver
import threading
import time
from unittest.mock import Mock, patch

import pytest

from arxiv_mcp_server.tools.download import _fetch_arxiv_metadata

FEED_XML = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/1234.5678v1</id>
  <title>Test Paper</title>
  <summary>Test summary</summary>
  <published>2024-01-01T00:00:00Z</published>
  <author><name>Test Author</name></author>
  <arxiv:primary_category term="cs.LG"/>
</entry>
</feed>"""


class SlowHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler with controllable delays for testing deadline enforcement."""

    mode = "fast"
    request_count = 0

    def log_message(self, *args):
        """Suppress HTTP server logs during tests."""
        pass

    def do_GET(self):
        """Handle GET request with delays based on mode."""
        SlowHandler.request_count += 1

        if self.path.startswith("/metadata"):
            if SlowHandler.mode == "trickle":
                # Slow trickle: send 10 bytes every 0.5s (total ~5.5s for 110 bytes)
                self.send_response(200)
                self.send_header("Content-Type", "application/atom+xml")
                self.send_header("Content-Length", str(len(FEED_XML)))
                self.end_headers()
                chunk_size = 10
                for i in range(0, len(FEED_XML), chunk_size):
                    chunk = FEED_XML[i : i + chunk_size]
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        if i + chunk_size < len(FEED_XML):
                            time.sleep(0.5)
                    except (BrokenPipeError, ConnectionResetError):
                        break
            elif SlowHandler.mode == "trickle-then-stall":
                # Trickle then stall: 6 bytes at 0.5s intervals, then 25s silence
                self.send_response(200)
                self.send_header("Content-Type", "application/atom+xml")
                self.send_header("Content-Length", str(len(FEED_XML)))
                self.end_headers()
                for i in range(6):
                    try:
                        self.wfile.write(FEED_XML[i : i + 1])
                        self.wfile.flush()
                        time.sleep(0.5)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                # Now stall for 25s (watchdog should kill this)
                time.sleep(25.0)
                # If we get here, watchdog failed
                try:
                    self.wfile.write(FEED_XML[6:])
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif SlowHandler.mode == "byte-by-byte-slow":
                # 1 byte every 0.5s (total ~55s for 110 bytes)
                self.send_response(200)
                self.send_header("Content-Type", "application/atom+xml")
                self.send_header("Content-Length", str(len(FEED_XML)))
                self.end_headers()
                for byte in FEED_XML:
                    try:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.5)
                    except (BrokenPipeError, ConnectionResetError):
                        break
            elif SlowHandler.mode == "headers-stall":
                # Stall before sending headers (should timeout on connect/headers)
                time.sleep(25.0)
                self.send_response(200)
                self.send_header("Content-Type", "application/atom+xml")
                self.send_header("Content-Length", str(len(FEED_XML)))
                self.end_headers()
                self.wfile.write(FEED_XML)
            elif SlowHandler.mode == "headers-then-stall":
                # Send headers immediately, then stall with no body bytes
                self.send_response(200)
                self.send_header("Content-Type", "application/atom+xml")
                self.send_header("Content-Length", str(len(FEED_XML)))
                self.end_headers()
                # Stall for 25s without sending any body
                time.sleep(25.0)
                # If we get here, backstop failed
                try:
                    self.wfile.write(FEED_XML)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                # Fast mode: return immediately
                self.send_response(200)
                self.send_header("Content-Type", "application/atom+xml")
                self.send_header("Content-Length", str(len(FEED_XML)))
                self.end_headers()
                self.wfile.write(FEED_XML)
        else:
            self.send_error(404)


@pytest.fixture
def slow_server():
    """Start a local HTTP server on 127.0.0.1 with a random port."""
    from socketserver import ThreadingTCPServer

    server = ThreadingTCPServer(("127.0.0.1", 0), SlowHandler)
    server.daemon_threads = True
    server.block_on_close = False

    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    SlowHandler.request_count = 0

    yield f"http://127.0.0.1:{port}"

    server.shutdown()
    server.server_close()


def test_trickle_returns_none_within_deadline(slow_server):
    """Slow trickle (10 bytes/0.5s) should return None within 3s deadline."""
    SlowHandler.mode = "trickle"
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 3.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            return original_get(slow_server + "/metadata", **kwargs)
        return original_get(url, **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    with (
        patch("requests.get", patched_get),
        patch.object(ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0),
        patch.object(ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()),
    ):
        result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    elapsed = time.monotonic() - start

    assert result is None, f"Expected None, got {result}"
    assert elapsed < 3.5, f"Trickle took {elapsed:.2f}s, expected < 3.5s"
    assert (
        SlowHandler.request_count == 1
    ), f"Expected 1 request, got {SlowHandler.request_count}"


def test_trickle_then_stall_returns_none_within_deadline(slow_server):
    """Trickle then 25s stall should return None within 4s deadline (watchdog)."""
    SlowHandler.mode = "trickle-then-stall"
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 4.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            return original_get(slow_server + "/metadata", **kwargs)
        return original_get(url, **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    with (
        patch("requests.get", patched_get),
        patch.object(ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0),
        patch.object(ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()),
    ):
        result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    elapsed = time.monotonic() - start

    assert result is None, f"Expected None, got {result}"
    assert elapsed < 4.5, f"Trickle-then-stall took {elapsed:.2f}s, expected < 4.5s"
    assert (
        SlowHandler.request_count == 1
    ), f"Expected 1 request, got {SlowHandler.request_count}"


def test_byte_by_byte_slow_returns_none_within_deadline(slow_server):
    """1 byte every 0.5s should return None within 3s deadline."""
    SlowHandler.mode = "byte-by-byte-slow"
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 3.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            return original_get(slow_server + "/metadata", **kwargs)
        return original_get(url, **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    with (
        patch("requests.get", patched_get),
        patch.object(ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0),
        patch.object(ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()),
    ):
        result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    elapsed = time.monotonic() - start

    assert result is None, f"Expected None, got {result}"
    assert elapsed < 3.5, f"Byte-by-byte took {elapsed:.2f}s, expected < 3.5s"
    assert (
        SlowHandler.request_count == 1
    ), f"Expected 1 request, got {SlowHandler.request_count}"


def test_socket_unavailable_fallback_within_deadline(slow_server):
    """When socket lookup fails (hidden _fp), thread backstop enforces deadline on stall."""
    SlowHandler.mode = "headers-then-stall"  # Send headers, then stall with no bytes
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 4.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            response = original_get(slow_server + "/metadata", **kwargs)
            # Replace raw with a mock that makes sock lookup return None but keeps streaming working
            original_raw = response.raw
            mock_raw = Mock()
            mock_raw._fp = Mock()
            mock_raw._fp.fp = Mock()
            mock_raw._fp.fp.raw = Mock()
            mock_raw._fp.fp.raw._sock = None  # Make sock lookup return None
            # Delegate everything else to original
            mock_raw.read = original_raw.read
            mock_raw.stream = original_raw.stream
            response.raw = mock_raw
            return response
        return original_get(url, **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    with (
        patch("requests.get", patched_get),
        patch.object(ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0),
        patch.object(ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()),
    ):
        result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    elapsed = time.monotonic() - start

    # Thread backstop should enforce deadline
    assert result is None, f"Expected None, got {result}"
    assert (
        3.5 <= elapsed < 4.5
    ), f"Socket-unavailable stall took {elapsed:.2f}s, expected 3.5-4.5s"
    assert (
        SlowHandler.request_count == 1
    ), f"Expected 1 request, got {SlowHandler.request_count}"


def test_socket_timeout_mechanism_alone_enforces_deadline(slow_server):
    """Socket timeout alone (watchdog and per-byte check disabled) enforces deadline."""
    import os

    SlowHandler.mode = "headers-then-stall"  # Send headers, then stall
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 3.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            return original_get(slow_server + "/metadata", **kwargs)
        return original_get(url, **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    # Disable watchdog and per-byte check via injectable flags
    os.environ["_ARXIV_MCP_TEST_SKIP_WATCHDOG"] = "1"
    os.environ["_ARXIV_MCP_TEST_SKIP_PERBYTE_CHECK"] = "1"
    try:
        with (
            patch("requests.get", patched_get),
            patch.object(
                ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0
            ),
            patch.object(
                ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()
            ),
        ):
            result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

        elapsed = time.monotonic() - start

        # Socket timeout mechanism should enforce deadline
        assert result is None, f"Expected None, got {result}"
        assert (
            2.5 <= elapsed < 3.5
        ), f"Socket timeout took {elapsed:.2f}s, expected 2.5-3.5s"
        assert SlowHandler.request_count == 1
    finally:
        os.environ.pop("_ARXIV_MCP_TEST_SKIP_WATCHDOG", None)
        os.environ.pop("_ARXIV_MCP_TEST_SKIP_PERBYTE_CHECK", None)


def test_watchdog_mechanism_alone_enforces_deadline(slow_server):
    """Watchdog alone (socket timeout and per-byte check disabled) enforces deadline."""
    import os

    SlowHandler.mode = "trickle-then-stall"  # Trickle then 25s stall
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 4.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            response = original_get(slow_server + "/metadata", **kwargs)
            # Keep real sock - just disable the other mechanisms
            return original_get(slow_server + "/metadata", **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    # Disable socket timeout and per-byte check
    os.environ["_ARXIV_MCP_TEST_SKIP_SOCKET_TIMEOUT"] = "1"
    os.environ["_ARXIV_MCP_TEST_SKIP_PERBYTE_CHECK"] = "1"
    try:
        with (
            patch("requests.get", patched_get),
            patch.object(
                ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0
            ),
            patch.object(
                ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()
            ),
        ):
            result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

        elapsed = time.monotonic() - start

        # Watchdog should enforce deadline at ~3.2s (deadline - 0.8)
        assert result is None, f"Expected None, got {result}"
        assert elapsed < 4.5, f"Watchdog took {elapsed:.2f}s, expected < 4.5s"
        assert SlowHandler.request_count == 1
    finally:
        os.environ.pop("_ARXIV_MCP_TEST_SKIP_SOCKET_TIMEOUT", None)
        os.environ.pop("_ARXIV_MCP_TEST_SKIP_PERBYTE_CHECK", None)


def test_per_byte_check_alone_enforces_deadline(slow_server):
    """Per-byte check alone (watchdog and socket timeout disabled) enforces deadline."""
    import os

    SlowHandler.mode = "trickle"  # 10 bytes every 0.5s
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 3.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            response = original_get(slow_server + "/metadata", **kwargs)
            # Keep real sock - just disable the other mechanisms
            return original_get(slow_server + "/metadata", **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    # Disable watchdog and socket timeout
    os.environ["_ARXIV_MCP_TEST_SKIP_WATCHDOG"] = "1"
    os.environ["_ARXIV_MCP_TEST_SKIP_SOCKET_TIMEOUT"] = "1"
    try:
        with (
            patch("requests.get", patched_get),
            patch.object(
                ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0
            ),
            patch.object(
                ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()
            ),
        ):
            result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

        elapsed = time.monotonic() - start

        # Per-byte check should enforce deadline
        assert result is None, f"Expected None, got {result}"
        assert (
            2.5 <= elapsed < 3.5
        ), f"Per-byte check took {elapsed:.2f}s, expected 2.5-3.5s"
        assert SlowHandler.request_count == 1
    finally:
        os.environ.pop("_ARXIV_MCP_TEST_SKIP_WATCHDOG", None)
        os.environ.pop("_ARXIV_MCP_TEST_SKIP_SOCKET_TIMEOUT", None)


def test_headers_stall_returns_none_within_deadline(slow_server):
    """25s stall before headers should return None within 3s deadline."""
    SlowHandler.mode = "headers-stall"
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 3.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            return original_get(slow_server + "/metadata", **kwargs)
        return original_get(url, **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    with (
        patch("requests.get", patched_get),
        patch.object(ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0),
        patch.object(ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()),
    ):
        result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    elapsed = time.monotonic() - start

    assert result is None, f"Expected None, got {result}"
    assert elapsed < 3.5, f"Headers stall took {elapsed:.2f}s, expected < 3.5s"
    assert (
        SlowHandler.request_count == 1
    ), f"Expected 1 request, got {SlowHandler.request_count}"


def test_fast_response_succeeds(slow_server):
    """Fast response should parse successfully within deadline."""
    SlowHandler.mode = "fast"
    SlowHandler.request_count = 0

    start = time.monotonic()
    deadline = start + 3.0

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            return original_get(slow_server + "/metadata", **kwargs)
        return original_get(url, **kwargs)

    from arxiv_mcp_server.tools.download import ARXIV_RATE_LIMITER

    with (
        patch("requests.get", patched_get),
        patch.object(ARXIV_RATE_LIMITER, "seconds_until_next_slot", return_value=0.0),
        patch.object(ARXIV_RATE_LIMITER, "run_sync", side_effect=lambda f, **kw: f()),
    ):
        result = _fetch_arxiv_metadata("1234.5678", deadline=deadline)

    elapsed = time.monotonic() - start

    assert result is not None
    assert result["title"] == "Test Paper"
    assert result["arxiv_version"] == "v1"
    assert elapsed < 1.0, f"Fast response took {elapsed:.2f}s"
    assert SlowHandler.request_count == 1
