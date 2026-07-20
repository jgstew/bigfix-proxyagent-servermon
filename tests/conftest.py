"""Shared fixtures: a local HTTP server with known responses, and helpers."""

from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

from servermon.checker import MAX_BODY_BYTES


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "servermon-test/1.0"
    sys_version = ""

    # /flaky fails on the first hit and succeeds afterwards, to test that
    # the last error is remembered after a transient failure clears.
    flaky_hits = 0

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path == "/ok":
            body = b"hello from the servermon test server"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Test-Header", "header-needle")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/flaky":
            type(self).flaky_hits += 1
            body = b"flaky ok" if type(self).flaky_hits > 1 else b"flaky failure"
            self.send_response(200 if type(self).flaky_hits > 1 else 500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":
            # Accepts the connection but stalls the response, to test that a
            # too-slow server surfaces as a timeout error, not a hang.
            time.sleep(2)
            try:
                body = b"finally"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass  # client gave up long ago
        elif self.path == "/big":
            # MAX_BODY_BYTES of filler, then a needle the byte cap must hide.
            body = b"x" * MAX_BODY_BYTES + b"needle-beyond-cap"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/latin":
            # Declares a charset Python does not know, to test the decode
            # fallback path.
            body = b"gr\xfcn body-needle"  # latin-1 bytes, invalid as UTF-8
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=klingon-piqad")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/error":
            body = b"internal problem"
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture(scope="session")
def http_server() -> str:
    """Base URL of a local test HTTP server."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="session")
def closed_port_url() -> str:
    """A URL on a port where nothing is listening."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/"
