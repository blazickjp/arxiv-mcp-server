#!/usr/bin/env python3
"""Test hidden-_fp stall (headers then no bytes) stays within budget."""

import http.server
import socketserver
import threading
import time
from unittest.mock import patch

from arxiv_mcp_server.tools.download import _fetch_arxiv_metadata

FEED_XML = b"""<?xml version="1.0"?>
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


class StallHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/metadata"):
            # Send headers immediately, then stall
            self.send_response(200)
            self.send_header("Content-Type", "application/atom+xml")
            self.send_header("Content-Length", str(len(FEED_XML)))
            self.end_headers()
            # Stall for 25s without sending body
            time.sleep(25.0)


def test_stall(deadline_seconds):
    """Test hidden-_fp stall with given deadline."""
    from socketserver import ThreadingTCPServer

    server = ThreadingTCPServer(("127.0.0.1", 0), StallHandler)
    server.daemon_threads = True
    server.block_on_close = False

    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    server_url = f"http://127.0.0.1:{port}"

    start = time.monotonic()
    deadline = start + deadline_seconds

    original_get = __import__("requests").get

    def patched_get(url, **kwargs):
        if "export.arxiv.org" in url:
            response = original_get(server_url + "/metadata", **kwargs)
            # Replace raw with mock that makes sock lookup return None but keeps streaming
            original_raw = response.raw
            mock_raw = Mock()
            mock_raw._fp = Mock()
            mock_raw._fp.fp = Mock()
            mock_raw._fp.fp.raw = Mock()
            mock_raw._fp.fp.raw._sock = None
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
    server.shutdown()
    server.server_close()

    return elapsed, result


if __name__ == "__main__":
    print("Testing hidden-_fp stall cases:")
    print()

    # Test at 4s deadline
    elapsed_4s, result_4s = test_stall(4.0)
    status_4s = "✓ PASS" if result_4s is None and elapsed_4s <= 4.5 else "✗ FAIL"
    print(
        f"4s deadline: {elapsed_4s:.2f}s elapsed, result={result_4s is None} {status_4s}"
    )

    # Test at 7s deadline
    elapsed_7s, result_7s = test_stall(7.0)
    status_7s = "✓ PASS" if result_7s is None and elapsed_7s <= 7.5 else "✗ FAIL"
    print(
        f"7s deadline: {elapsed_7s:.2f}s elapsed, result={result_7s is None} {status_7s}"
    )

    if (
        result_4s is None
        and elapsed_4s <= 4.5
        and result_7s is None
        and elapsed_7s <= 7.5
    ):
        print("\n✓ All hidden-_fp stall tests passed")
        exit(0)
    else:
        print("\n✗ Some tests failed")
        exit(1)
