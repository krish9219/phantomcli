"""
Real Flask integration tests for the smoke-test + launch detection logic.

Spins up three real Flask apps as subprocesses on ephemeral ports:
  1. A healthy app (returns 200 + rich HTML)
  2. A 500 app (route raises an uncaught exception)
  3. A crashed app (500 with a Python traceback rendered in the body)

Each is pointed at via _smoke_test_url() and we assert the verdict.
Skipped if flask isn't installed in the venv.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _write_app(dirpath: Path, body: str) -> Path:
    """Write app.py with the given route body; wrap with the Flask boilerplate."""
    app = (
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route('/')\n"
        "def index():\n"
        + textwrap.indent(body, '    ') +
        "\n"
        "if __name__ == '__main__':\n"
        "    import sys\n"
        "    app.run(host='127.0.0.1', port=int(sys.argv[1]), debug=True)\n"
    )
    path = dirpath / "app.py"
    path.write_text(app)
    return path


def _launch_flask(app_path: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(app_path), str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, text=True,
    )
    # Wait up to 8s for the port to bind
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
            s.close()
            return proc
        except OSError:
            time.sleep(0.1)
        if proc.poll() is not None:
            break
    # Didn't bind — terminate + raise
    try: proc.terminate()
    except Exception: pass
    raise RuntimeError(f"Flask app never bound to port {port}")


@pytest.fixture
def healthy_app(tmp_path):
    body = 'return "<html><body><h1>Welcome to Phantom</h1><p>Rich content for everyone.</p>" + ("padding " * 200) + "</body></html>"'
    app_path = _write_app(tmp_path, body)
    port = _free_port()
    proc = _launch_flask(app_path, port)
    yield {"url": f"http://127.0.0.1:{port}/", "port": port, "proc": proc}
    try: proc.terminate(); proc.wait(timeout=3)
    except Exception:
        try: proc.kill()
        except Exception: pass


@pytest.fixture
def broken_app(tmp_path):
    body = (
        'x = {}\n'
        'return x["missing_key"]  # KeyError triggers a 500 with traceback\n'
    )
    app_path = _write_app(tmp_path, body)
    port = _free_port()
    proc = _launch_flask(app_path, port)
    yield {"url": f"http://127.0.0.1:{port}/", "port": port, "proc": proc}
    try: proc.terminate(); proc.wait(timeout=3)
    except Exception:
        try: proc.kill()
        except Exception: pass


@pytest.fixture
def tiny_body_app(tmp_path):
    body = 'return "hi"'  # body too short
    app_path = _write_app(tmp_path, body)
    port = _free_port()
    proc = _launch_flask(app_path, port)
    yield {"url": f"http://127.0.0.1:{port}/", "port": port, "proc": proc}
    try: proc.terminate(); proc.wait(timeout=3)
    except Exception:
        try: proc.kill()
        except Exception: pass


# ─── Tests ────────────────────────────────────────────────────────────────────


from omnicli.cli import _smoke_test_url  # noqa: E402


class TestRealFlaskHealthy:
    def test_healthy_app_passes_smoke(self, healthy_app):
        ok, status, snippet, reason = _smoke_test_url(healthy_app["url"], timeout=5.0)
        assert ok is True, f"unexpected failure: {reason!r}"
        assert status == 200
        assert "Welcome to Phantom" in snippet


class TestRealFlaskBroken:
    def test_500_with_traceback_fails_smoke(self, broken_app):
        # Flask debug=True renders a 500 page WITH a traceback
        ok, status, snippet, reason = _smoke_test_url(broken_app["url"], timeout=5.0)
        assert ok is False, f"expected failure but got ok (reason={reason!r})"
        # Either non-2xx or a traceback marker was detected
        assert status != 200 or "traceback" in reason.lower() or "marker" in reason.lower()


class TestRealFlaskTinyBody:
    def test_200_but_tiny_body_fails(self, tiny_body_app):
        ok, status, snippet, reason = _smoke_test_url(tiny_body_app["url"], timeout=5.0)
        assert ok is False
        # Either status was ok but body too short
        assert "chars" in reason.lower() or "body" in reason.lower() or status != 200


class TestUnreachable:
    def test_unreachable_port_fails_fast(self):
        # Free a port, don't start anything on it
        port = _free_port()
        ok, status, _, reason = _smoke_test_url(
            f"http://127.0.0.1:{port}/", timeout=2.0,
        )
        assert ok is False
        assert status == 0
        assert "connection" in reason.lower() or "refused" in reason.lower()
