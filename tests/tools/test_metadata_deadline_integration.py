"""Integration tests for metadata deadline enforcement with a local slow socket server.

These tests use a real HTTP server with controlled delays to verify that:
1. The watchdog timer correctly interrupts stalled reads
2. The per-byte deadline check catches slow trickles
3. Both mechanisms fail gracefully and return None within the deadline

Mutation tests:
- Disabling the watchdog should cause trickle-then-stall to fail (overrun)
- Disabling the per-read deadline check should cause slow trickle to fail (overrun)
"""

import http.server
import socketserver
import threading
import time
from unittest.mock import patch

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
    with socketserver.TCPServer(("127.0.0.1", 0), SlowHandler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        SlowHandler.request_count = 0

        yield f"http://127.0.0.1:{port}"

        server.shutdown()


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
