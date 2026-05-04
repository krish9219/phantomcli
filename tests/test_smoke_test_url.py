"""Tests for cli._smoke_test_url — the post-launch health check.

These tests spin up a tiny HTTP server inside the test process on an
ephemeral port and point the smoke test at it. Real network, no mocks —
that's the only way to trust the urllib-based implementation."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def _make_handler(status: int, body: bytes, delay: float = 0.0):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a, **_kw): pass  # silence noise
        def do_GET(self):
            if delay:
                import time; time.sleep(delay)
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return H


@pytest.fixture
def serve():
    """Spin up an HTTPServer in a background thread, return (url, stop_fn)."""
    servers = []
    def _factory(status: int, body: bytes, delay: float = 0.0):
        h = _make_handler(status, body, delay)
        srv = HTTPServer(("127.0.0.1", 0), h)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)
        return f"http://127.0.0.1:{port}/"
    yield _factory
    for s in servers:
        s.shutdown()
        s.server_close()


# Import at module load — fails the whole file early if cli.py is broken
from omnicli import cli as _cli  # noqa: E402


class TestHappyPath:
    def test_200_with_rich_body_is_ok(self, serve):
        url = serve(200, b"<html>" + b"x" * 500 + b"</html>")
        ok, status, snippet, reason = _cli._smoke_test_url(url, timeout=3.0)
        assert ok is True
        assert status == 200
        assert reason == "ok"


class TestFailureModes:
    def test_500_fails(self, serve):
        url = serve(500, b"<h1>Internal Server Error</h1>" + b"x" * 200)
        ok, status, _, reason = _cli._smoke_test_url(url, timeout=3.0)
        assert ok is False
        # 500 triggers HTTPError path; reason mentions the status
        assert "500" in reason or "error" in reason.lower()

    def test_404_fails(self, serve):
        url = serve(404, b"<h1>Not Found</h1>" + b"x" * 200)
        ok, status, _, reason = _cli._smoke_test_url(url, timeout=3.0)
        assert ok is False

    def test_empty_body_fails(self, serve):
        url = serve(200, b"")
        ok, status, _, reason = _cli._smoke_test_url(url, timeout=3.0)
        assert ok is False
        assert "body" in reason.lower() or "chars" in reason.lower()

    def test_tiny_body_fails(self, serve):
        url = serve(200, b"ok")
        ok, _, _, reason = _cli._smoke_test_url(url, timeout=3.0)
        assert ok is False

    def test_traceback_in_body_fails(self, serve):
        body = (
            b"<html><pre>Traceback (most recent call last):\n"
            b"  File 'x', line 1\nNameError: name 'foo' is not defined</pre></html>"
            + b"x" * 200
        )
        url = serve(200, body)
        ok, _, _, reason = _cli._smoke_test_url(url, timeout=3.0)
        assert ok is False
        assert "traceback" in reason.lower() or "marker" in reason.lower()

    def test_werkzeug_error_detected(self, serve):
        body = b"<html>" + b"x" * 300 + b" werkzeug.exceptions.HTTPException raised " + b"y" * 100 + b"</html>"
        url = serve(200, body)
        ok, _, _, reason = _cli._smoke_test_url(url, timeout=3.0)
        assert ok is False
        assert "marker" in reason.lower() or "werkzeug" in reason.lower()

    def test_connection_refused_fails(self):
        # No server listening on this port.
        ok, status, _, reason = _cli._smoke_test_url(
            "http://127.0.0.1:1/", timeout=2.0
        )
        assert ok is False
        assert status == 0
        assert "connection" in reason.lower() or "refused" in reason.lower()


class TestLogTailReader:
    def test_reads_last_n_chars(self, tmp_path):
        p = tmp_path / "app.log"
        p.write_text("A" * 100 + "\n" + "B" * 2000 + "\n")
        tail = _cli._read_log_tail(str(p), max_chars=500)
        # Must contain only the tail, never the leading A block
        assert "A" * 100 not in tail
        assert "B" in tail

    def test_missing_file_returns_empty(self):
        assert _cli._read_log_tail("/nonexistent/log/path.log") == ""

    def test_smaller_than_max_returns_everything(self, tmp_path):
        p = tmp_path / "tiny.log"
        p.write_text("hello world")
        assert _cli._read_log_tail(str(p), max_chars=1000) == "hello world"
