"""End-to-end tests for the daemon server + client.

Spawns a real DaemonServer in a thread, talks to it over a real unix
socket, verifies wire format and op dispatch.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from phantom._version import __version__
from phantom.daemon import (
    DaemonRequest,
    DaemonResponse,
    decode_response,
    encode_request,
)
from phantom.daemon.client import DaemonClient, DaemonNotRunning
from phantom.daemon.server import DaemonServer, build_default_server, register_op


@pytest.fixture
def socket_path(tmp_path: Path) -> str:
    return str(tmp_path / "phantom.sock")


@pytest.fixture
def running_server(socket_path: str):
    server = build_default_server(socket_path=socket_path)
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    # Wait up to 2 s for the daemon to be ACCEPTING — the socket file
    # appearing isn't enough on macOS, where connect() refuses until
    # the server has called listen()+accept(). Probe with a real ping.
    accepting = False
    for _ in range(200):
        if Path(socket_path).exists():
            try:
                DaemonClient(socket_path=socket_path).call("ping")
                accepting = True
                break
            except DaemonNotRunning:
                pass
        time.sleep(0.01)
    assert accepting, "daemon failed to accept connections within 2 s"
    yield server
    server.stop()
    t.join(timeout=2)


def test_protocol_request_roundtrip():
    req = DaemonRequest(op="echo", payload={"text": "hi"})
    line = encode_request(req)
    assert line.endswith(b"\n")
    assert b"echo" in line


def test_protocol_response_roundtrip():
    line = b'{"ok": true, "result": {"x": 1}}\n'
    resp = decode_response(line)
    assert resp.ok is True
    assert resp.result == {"x": 1}


def test_protocol_response_error():
    line = b'{"ok": false, "error": "boom"}\n'
    resp = decode_response(line)
    assert resp.ok is False
    assert resp.error == "boom"


def test_client_raises_when_no_daemon(socket_path: str):
    client = DaemonClient(socket_path=socket_path)
    with pytest.raises(DaemonNotRunning):
        client.call("ping")


def test_ping(running_server, socket_path: str):
    resp = DaemonClient(socket_path=socket_path).call("ping")
    assert resp.ok is True
    assert resp.result["pid"] == os.getpid() or resp.result["pid"] > 0
    assert resp.result["uptime_s"] >= 0


def test_version(running_server, socket_path: str):
    resp = DaemonClient(socket_path=socket_path).call("version")
    assert resp.ok is True
    assert resp.result["version"] == __version__


def test_echo(running_server, socket_path: str):
    resp = DaemonClient(socket_path=socket_path).call("echo", text="hello world")
    assert resp.ok is True
    assert resp.result == {"text": "hello world"}


def test_unknown_op_returns_error(running_server, socket_path: str):
    resp = DaemonClient(socket_path=socket_path).call("does-not-exist")
    assert resp.ok is False
    assert "unknown op" in resp.error


def test_register_custom_op(running_server, socket_path: str):
    register_op("double", lambda p: {"v": p.get("v", 0) * 2})
    resp = DaemonClient(socket_path=socket_path).call("double", v=21)
    assert resp.ok is True
    assert resp.result == {"v": 42}


def test_handler_exception_becomes_error_response(running_server, socket_path: str):
    register_op("boom", lambda p: (_ for _ in ()).throw(RuntimeError("nope")))
    resp = DaemonClient(socket_path=socket_path).call("boom")
    assert resp.ok is False
    assert "RuntimeError" in resp.error
    assert "nope" in resp.error


def test_socket_permissions_are_owner_only(running_server, socket_path: str):
    mode = os.stat(socket_path).st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_daemon_perceived_start_under_50ms(running_server, socket_path: str):
    """Headline benchmark: a connect + ping must complete in <50ms."""
    # warm one round so any first-time socket setup is paid
    DaemonClient(socket_path=socket_path).call("ping")
    t0 = time.perf_counter()
    resp = DaemonClient(socket_path=socket_path).call("ping")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert resp.ok
    assert elapsed_ms < 50, f"daemon round-trip took {elapsed_ms:.2f}ms"
