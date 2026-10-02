#!/usr/bin/env python3
"""Misbehaving HTTP server for timing tests simulating pre-merge test conditions."""

import asyncio
import email.utils
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

class MisbehavingHandler(BaseHTTPRequestHandler):
    """HTTP handler that simulates various failure modes."""
    
    def log_message(self, format, *args):
        """Suppress logging."""
        pass
    
    def do_GET(self):
        if "/stall" in self.path:
            # Stall for 100s (simulates slow backend)
            time.sleep(100)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"finally")
        elif "/trickle" in self.path:
            # Trickle response slowly
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            for i in range(20):
                self.wfile.write(b"x" * 1000)
                self.wfile.flush()
                time.sleep(5)  # 5s between chunks = 100s total
        elif "/429-seconds" in self.path:
            # 429 with Retry-After in seconds
            self.send_response(429)
            self.send_header("Retry-After", "30")
            self.end_headers()
        elif "/429-httpdate" in self.path:
            # 429 with Retry-After as HTTP-date
            future = datetime.now(timezone.utc).timestamp() + 30
            http_date = email.utils.formatdate(future, usegmt=True)
            self.send_response(429)
            self.send_header("Retry-After", http_date)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    """Run misbehaving server in background thread."""
    server = HTTPServer(("127.0.0.1", 8765), MisbehavingHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

if __name__ == "__main__":
    server = run_server()
    print("Misbehaving HTTP server running on http://127.0.0.1:8765")
    print("Endpoints:")
    print("  /stall - 100s delay")
    print("  /trickle - slow trickling response")
    print("  /429-seconds - 429 with Retry-After: 30")
    print("  /429-httpdate - 429 with Retry-After as HTTP-date")
    print("\nPress Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
        print("\nServer stopped")
