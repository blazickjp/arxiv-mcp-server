#!/usr/bin/env python3
"""In-process repro for metadata deadline enforcement (no stdio MCP needed)."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from arxiv_mcp_server.tools.download import _fetch_arxiv_metadata
from arxiv_mcp_server.arxiv_api import ArxivRateLimiter

# Override rate limiter for repro
import arxiv_mcp_server.tools.download as download_module

download_module.ARXIV_RATE_LIMITER = ArxivRateLimiter(min_interval=0.1)

ATOM = b'''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/1234.5678v1</id>
  <title>Test Paper</title>
  <summary>Test summary</summary>
  <author><name>Test Author</name></author>
  <arxiv:primary_category term="cs.LG"/>
</entry>
</feed>'''

EVENTS = []
MODE = "406"  # Will be set by command line


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        EVENTS.append((self.path, time.monotonic()))
        if MODE == "406":
            # HTTP 406 response
            self.send_response(406)
            self.send_header("Retry-After", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            # Trickle response: send ATOM in small chunks slowly
            self.send_response(200)
            self.send_header("Content-Type", "application/atom+xml")
            self.send_header("Content-Length", str(len(ATOM)))
            self.end_headers()
            # Send 10-byte chunks every 0.75s
            chunk_size = 10
            for i in range(0, len(ATOM), chunk_size):
                chunk = ATOM[i : i + chunk_size]
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if i + chunk_size < len(ATOM):
                        time.sleep(0.75)
                except (BrokenPipeError, ConnectionResetError):
                    break


def run_test(mode, budget=7.0):
    global MODE, EVENTS
    MODE = mode
    EVENTS = []

    # Start HTTP server
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Patch the URL to point to local server
        import arxiv_mcp_server.tools.download as dl

        original_url_template = "https://export.arxiv.org/api/query?id_list={}"

        def patched_fetch(paper_id, deadline=None):
            # Temporarily patch the URL in the function
            import unittest.mock as mock

            def fetch_impl():
                url = f"http://127.0.0.1:{port}/api/query?id_list={paper_id}"
                # Call original with patched requests
                with mock.patch("requests.get") as mock_get:
                    import requests

                    def redirect_get(orig_url, **kwargs):
                        if "export.arxiv.org" in orig_url:
                            return requests.get(url, **kwargs)
                        return requests.get(orig_url, **kwargs)

                    # Use real requests but redirect the URL
                    import requests as real_requests

                    mock_get.side_effect = lambda u, **kw: real_requests.get(
                        url, **kw
                    )
                    return dl._fetch_arxiv_metadata(paper_id, deadline)

            return fetch_impl()

        # Run test
        started = time.monotonic()
        deadline = started + budget
        result = patched_fetch("1234.5678", deadline=deadline)
        elapsed = time.monotonic() - started

        metadata_requests = len([e for e in EVENTS if "/api/query" in e[0]])

        print(f"\n{'='*60}")
        print(f"Mode: {mode}")
        print(f"Budget: {budget}s")
        print(f"Elapsed: {elapsed:.3f}s")
        print(f"Metadata requests: {metadata_requests}")
        print(f"Result: {'SUCCESS' if result is None else 'METADATA'}")
        print(f"{'='*60}")

        # Assertions
        if elapsed > budget + 0.5:
            print(
                f"❌ FAILED: Elapsed {elapsed:.3f}s exceeds budget+0.5s ({budget+0.5}s)"
            )
            return False
        if metadata_requests != 1:
            print(
                f"❌ FAILED: Expected 1 metadata request, got {metadata_requests}"
            )
            return False

        print(f"✓ PASSED: elapsed {elapsed:.3f}s <= {budget+0.5}s, requests = 1")
        return True

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    import os

    # Set environment for testing
    os.environ["ARXIV_MAX_TOTAL_TIME"] = "7"
    os.environ["ARXIV_REQUEST_TIMEOUT"] = "1"
    os.environ["ARXIV_CONNECT_TIMEOUT"] = "10"

    print("Testing metadata deadline enforcement...")
    print()

    passed_406 = run_test("406", budget=7.0)
    time.sleep(1)
    passed_trickle = run_test("trickle", budget=7.0)

    print()
    if passed_406 and passed_trickle:
        print("✅ All tests PASSED")
        sys.exit(0)
    else:
        print("❌ Some tests FAILED")
        sys.exit(1)
