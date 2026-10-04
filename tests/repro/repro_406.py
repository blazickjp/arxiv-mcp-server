"""Local fault injection for arxiv-mcp-server 0.8.0; no live arXiv traffic."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def run_server():
    import httpx
    import requests

    base = os.environ["REPRO_BASE"]
    original_get = httpx.get
    original_request = requests.sessions.Session.request

    def html_get(url, *args, **kwargs):
        if not str(url).startswith("https://arxiv.org/html/"):
            raise RuntimeError("Unexpected HTML URL")
        print("HTML timeout:", kwargs.get("timeout"), file=sys.stderr)
        return original_get(base + "/html", *args, **kwargs)

    def metadata_request(self, method, url, *args, **kwargs):
        if urlsplit(str(url)).hostname not in {"arxiv.org", "export.arxiv.org"}:
            raise RuntimeError("Unexpected metadata URL")
        print("Metadata timeout:", kwargs.get("timeout"), file=sys.stderr)
        return original_request(self, method, base + "/metadata", *args, **kwargs)

    # Redirect destinations only. Preserve timeout arguments, rate limiting,
    # retries, and all installed server logic. No package files are edited.
    httpx.get = html_get
    requests.sessions.Session.request = metadata_request
    sys.argv = [sys.argv[0], "--storage-path", sys.argv[2]]
    from arxiv_mcp_server import main

    main()


ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
 xmlns:arxiv="http://arxiv.org/schemas/atom">
 <id>http://arxiv.org/api/mock</id><title>Mock API</title>
 <updated>2026-10-04T00:00:00Z</updated>
 <opensearch:totalResults>1</opensearch:totalResults>
 <entry><id>https://arxiv.org/abs/2404.19756v5</id>
  <updated>2024-05-01T00:00:00Z</updated>
  <published>2024-04-30T00:00:00Z</published>
  <title>Synthetic timeout test</title><summary>Synthetic content only.</summary>
  <author><name>Test Author</name></author>
  <link href="https://arxiv.org/abs/2404.19756v5" rel="alternate" type="text/html"/>
  <category term="cs.LG"/><arxiv:primary_category term="cs.LG"/>
 </entry>
</feed>"""
EVENTS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        EVENTS.append((self.path, time.monotonic()))
        if self.path.startswith("/metadata"):
            self.send_response(406)
            self.send_header("Retry-After", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            body = (
                b"<article><h1>Synthetic paper</h1><h2>Introduction</h2><p>"
                + b"Local timeout test only. " * 50
                + b"</p></article>"
            )
            content_type = "text/html"
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


async def run_client(http_server, directory):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env.update(
        {
            "HOME": directory,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "REPRO_BASE": f"http://127.0.0.1:{http_server.server_port}",
            "ARXIV_MAX_TOTAL_TIME": "7",
            "ARXIV_REQUEST_TIMEOUT": "1",
            "ARXIV_MAX_RETRIES": "0",
            "ARXIV_HTTP_406_MAX_RETRIES": "0",
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            str(Path(__file__).resolve()),
            "--server",
            str(Path(directory) / "papers"),
        ],
        env=env,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            started = time.monotonic()
            result = await asyncio.wait_for(
                session.call_tool(
                    "download_paper",
                    {"paper_id": "2404.19756", "max_chars": 100, "force": True},
                ),
                timeout=20,
            )
            elapsed = time.monotonic() - started
            payload = json.loads(result.content[0].text)
            print(
                json.dumps(
                    {
                        "elapsed_seconds": round(elapsed, 3),
                        "budget_seconds": 7,
                        "configured_read_timeout_seconds": 1,
                        "status": payload.get("status"),
                        "events": [
                            {
                                "path": path,
                                "start_after_seconds": round(at - started, 3),
                            }
                            for path, at in EVENTS
                        ],
                    },
                    indent=2,
                )
            )
            alive = await session.call_tool("list_papers", {"compact": True})
            print("Server responsive afterward:", not alive.isError)


if __name__ == "__main__":
    if "--server" in sys.argv:
        run_server()
    else:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(
                prefix="arxiv-timeout-repro-"
            ) as directory:
                asyncio.run(run_client(server, directory))
        finally:
            server.shutdown()
            server.server_close()
