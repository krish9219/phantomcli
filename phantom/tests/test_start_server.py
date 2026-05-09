"""Tests for v1.1.20 start_server tool — true detached spawn + URL probe.

The motivating user trace (v1.1.19): the model called
``run_bash python app.py`` which blocked Flask in the foreground until
the 60s timeout killed it. No URL ever returned. The new
``start_server`` tool spawns the process detached, polls the port,
and returns immediately with the URL.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

from phantom.agent.tools import _guess_port, _start_server, default_tools


# ─── _guess_port ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,expected", [
    ("python app.py", 0),
    ("flask run", 0),
    ("flask run --port 5050", 5050),
    ("flask run -p 5051", 5051),
    ("uvicorn main:app", 8000),
    ("uvicorn main:app --port 9000", 9000),
    ("npm start", 0),
    ("next dev", 3000),
    ("rails server", 3000),
    ("./server --listen 0.0.0.0:8765", 8765),
    ("python -m http.server --port=4444", 4444),
])
def test_guess_port(cmd, expected):
    assert _guess_port(cmd) == expected


# ─── _start_server actually spawns + polls + returns ─────────────────────────

def _free_port() -> int:
    """Bind ephemerally to find a port nothing else is using."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server_script(tmp_path: Path) -> tuple[Path, int]:
    """A real, minimal Python HTTP server that listens immediately."""
    port = _free_port()
    src = (
        "import http.server\n"
        "import socketserver\n"
        f"PORT = {port}\n"
        "with socketserver.TCPServer(('127.0.0.1', PORT), http.server.SimpleHTTPRequestHandler) as h:\n"
        "    h.serve_forever()\n"
    )
    p = tmp_path / "server.py"
    p.write_text(src)
    return p, port


def test_start_server_returns_url_immediately(tmp_path: Path, server_script):
    _, port = server_script
    out = json.loads(_start_server(
        {"command": f"{sys.executable} server.py", "port": port, "wait_s": 5},
        workdir=str(tmp_path),
    ))
    assert out["url"] == f"http://127.0.0.1:{port}"
    assert out["pid"] > 0
    assert out["alive"] is True
    # Reaping the child so we don't leave background pythons running.
    try:
        if sys.platform == "win32":
            os.system(f"taskkill /PID {out['pid']} /F >NUL 2>&1")
        else:
            os.kill(out["pid"], 9)
    except (OSError, ProcessLookupError):
        pass


def test_start_server_creates_log_file(tmp_path: Path, server_script):
    _, port = server_script
    out = json.loads(_start_server(
        {"command": f"{sys.executable} server.py", "port": port, "wait_s": 1},
        workdir=str(tmp_path),
    ))
    log_path = Path(out["log"])
    assert log_path.exists()
    assert log_path.name == ".phantom_server.log"
    # cleanup
    try:
        if sys.platform == "win32":
            os.system(f"taskkill /PID {out['pid']} /F >NUL 2>&1")
        else:
            os.kill(out["pid"], 9)
    except (OSError, ProcessLookupError):
        pass


def test_start_server_reports_listening_true_when_port_open(tmp_path: Path, server_script):
    _, port = server_script
    out = json.loads(_start_server(
        {"command": f"{sys.executable} server.py", "port": port, "wait_s": 5},
        workdir=str(tmp_path),
    ))
    assert out["listening"] is True
    assert "tell the user to open" in out["hint"].lower()
    try:
        os.kill(out["pid"], 9) if sys.platform != "win32" else os.system(f"taskkill /PID {out['pid']} /F >NUL 2>&1")
    except (OSError, ProcessLookupError):
        pass


def test_start_server_detects_immediate_crash(tmp_path: Path):
    """A command that exits immediately (e.g. syntax error) → alive=False
    + a hint that points to the log file."""
    bad = tmp_path / "bad.py"
    bad.write_text("import does_not_exist\n")
    out = json.loads(_start_server(
        {"command": f"{sys.executable} bad.py", "port": 5000, "wait_s": 2},
        workdir=str(tmp_path),
    ))
    assert out["alive"] is False
    assert "Server exited" in out["hint"] or "exited" in out["hint"].lower()
    log = Path(out["log"])
    assert log.exists()
    # The log should mention the import error.
    contents = log.read_text(errors="ignore")
    assert "does_not_exist" in contents or "ModuleNotFoundError" in contents


def test_start_server_empty_command_returns_hint(tmp_path: Path):
    out = json.loads(_start_server({"command": ""}, workdir=str(tmp_path)))
    assert "error" in out
    assert "hint" in out


def test_start_server_uses_guessed_port_when_not_provided(tmp_path: Path):
    """Even when the script doesn't actually bind, _start_server should
    pick a sensible port for the URL based on the command pattern."""
    bad = tmp_path / "bad.py"
    bad.write_text("import time; time.sleep(0.1)\n")
    out = json.loads(_start_server(
        {"command": f"{sys.executable} bad.py", "wait_s": 0.5},
        workdir=str(tmp_path),
    ))
    # Default fallback is 5000 (Flask).
    assert out["port"] == 5000


# ─── tool registration ──────────────────────────────────────────────────────

def test_default_tools_includes_start_server(tmp_path: Path):
    names = [t.name for t in default_tools(workdir=str(tmp_path))]
    assert "start_server" in names


def test_start_server_schema_advertises_command_and_port(tmp_path: Path):
    tools = default_tools(workdir=str(tmp_path))
    server = next(t for t in tools if t.name == "start_server")
    props = server.input_schema["properties"]
    assert "command" in props
    assert "port" in props
    assert "wait_s" in props
    assert "command" in server.input_schema["required"]
    desc = server.description.lower()
    assert "detached" in desc or "background" in desc or "long-running" in desc
    assert "url" in desc
