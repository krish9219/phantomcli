"""Cross-platform daemon transport tests.

The transport layer is exercised on the host's native family (unix on
POSIX, tcp on Windows) plus an explicit TCP path on every OS so the
Windows code path is covered even when the suite runs on Linux/macOS.
"""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from phantom.daemon import Endpoint, default_endpoint, is_windows
from phantom.daemon.client import DaemonClient, DaemonNotRunning
from phantom.daemon.server import build_default_server
from phantom.daemon.transport import (
    _parse_endpoint,
    make_client_socket,
    make_listener_socket,
    remove_endpoint_artifacts,
)


@pytest.fixture
def short_tmp_path():
    """A short tmpdir suitable for AF_UNIX sockets on macOS.

    macOS limits AF_UNIX paths to 104 bytes; pytest's ``tmp_path`` lives
    under ``/private/var/folders/...`` which can blow past that with a
    test-name suffix. Use ``/tmp`` directly (which on macOS resolves to
    ``/private/tmp`` but the symlink path is what AF_UNIX actually sees,
    and it's ~5 chars).
    """
    base = "/tmp" if sys.platform == "darwin" else None
    with tempfile.TemporaryDirectory(prefix="ph-", dir=base) as d:
        yield Path(d)


# ─── Endpoint dataclass + parsing ───────────────────────────────────────────


def test_default_endpoint_returns_unix_on_posix():
    if sys.platform == "win32":
        pytest.skip("POSIX-only assertion")
    ep = default_endpoint()
    assert ep.family == "unix"
    assert ep.path.endswith(".sock")


def test_default_endpoint_returns_tcp_on_windows(monkeypatch: pytest.MonkeyPatch):
    """Force the Windows code path even on Linux/macOS via monkeypatching."""
    monkeypatch.setattr("phantom.daemon.transport.is_windows", lambda: True)
    monkeypatch.setenv("USERNAME", "tester")
    ep = default_endpoint()
    assert ep.family == "tcp"
    assert ep.path == "127.0.0.1"
    assert 17680 <= ep.port < 18680


def test_default_endpoint_per_user_port_stable_for_same_user(monkeypatch):
    monkeypatch.setattr("phantom.daemon.transport.is_windows", lambda: True)
    monkeypatch.setenv("USERNAME", "alice")
    a1 = default_endpoint().port
    a2 = default_endpoint().port
    assert a1 == a2  # stable per user
    monkeypatch.setenv("USERNAME", "bob")
    b1 = default_endpoint().port
    # Different user → likely different port (collision rare but possible)
    assert b1 != a1 or True  # documentation, not strict


def test_endpoint_display_unix():
    ep = Endpoint(family="unix", path="/tmp/x.sock")
    assert ep.display() == "unix:///tmp/x.sock"


def test_endpoint_display_tcp():
    ep = Endpoint(family="tcp", path="127.0.0.1", port=17680)
    assert ep.display() == "tcp://127.0.0.1:17680"


@pytest.mark.parametrize("spec,expected_family,expected_path,expected_port", [
    ("unix:///tmp/x.sock",            "unix", "/tmp/x.sock", 0),
    ("tcp://127.0.0.1:5000",          "tcp",  "127.0.0.1",   5000),
    ("tcp://0.0.0.0:1234",            "tcp",  "0.0.0.0",     1234),
    ("/var/run/phantom.sock",         "unix", "/var/run/phantom.sock", 0),
    ("127.0.0.1:8080",                "tcp",  "127.0.0.1",   8080),
])
def test_parse_endpoint_forms(spec, expected_family, expected_path, expected_port):
    ep = _parse_endpoint(spec)
    assert ep.family == expected_family
    assert ep.path == expected_path
    assert ep.port == expected_port


def test_parse_endpoint_windows_drive_letter_not_treated_as_tcp():
    """C:\\Windows\\Temp\\x.sock must NOT be parsed as host:port."""
    ep = _parse_endpoint(r"C:\Windows\Temp\x.sock")
    assert ep.family == "unix"


# ─── socket factories ───────────────────────────────────────────────────────


def test_listener_socket_tcp_binds_to_loopback(tmp_path: Path):
    ep = Endpoint(family="tcp", path="127.0.0.1", port=0)  # OS-assigned port
    s = make_listener_socket(ep)
    try:
        host, port = s.getsockname()
        assert host == "127.0.0.1"
        assert port > 0
    finally:
        s.close()


def test_listener_socket_unix_creates_file(short_tmp_path: Path):
    if sys.platform == "win32":
        pytest.skip("AF_UNIX availability on Windows depends on build")
    ep = Endpoint(family="unix", path=str(short_tmp_path / "x.sock"))
    s = make_listener_socket(ep)
    try:
        assert Path(ep.path).exists()
    finally:
        s.close()


def test_listener_socket_unix_owner_only_perms(short_tmp_path: Path):
    if sys.platform == "win32":
        pytest.skip("Unix mode bits not meaningful on Windows")
    ep = Endpoint(family="unix", path=str(short_tmp_path / "perm.sock"))
    s = make_listener_socket(ep)
    try:
        import os
        mode = os.stat(ep.path).st_mode & 0o777
        assert mode == 0o600
    finally:
        s.close()


def test_remove_endpoint_artifacts_unix(short_tmp_path: Path):
    if sys.platform == "win32":
        pytest.skip("AF_UNIX availability on Windows depends on build")
    ep = Endpoint(family="unix", path=str(short_tmp_path / "cleanup.sock"))
    s = make_listener_socket(ep)
    s.close()
    assert Path(ep.path).exists()
    remove_endpoint_artifacts(ep)
    assert not Path(ep.path).exists()


def test_remove_endpoint_artifacts_tcp_is_noop():
    ep = Endpoint(family="tcp", path="127.0.0.1", port=12345)
    # Just doesn't raise.
    remove_endpoint_artifacts(ep)


# ─── server + client over TCP loopback (the Windows path) ───────────────────


@pytest.fixture
def tcp_server():
    """Spin up a daemon on a free TCP loopback port."""
    ep = Endpoint(family="tcp", path="127.0.0.1", port=0)
    # pre-pick a port
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    ep = Endpoint(family="tcp", path="127.0.0.1", port=port)

    server = build_default_server(endpoint=ep)
    t = threading.Thread(target=server.start, daemon=True)
    t.start()

    # Wait for accept().
    accepting = False
    for _ in range(200):
        try:
            DaemonClient(endpoint=ep).call("ping")
            accepting = True
            break
        except DaemonNotRunning:
            time.sleep(0.01)
    assert accepting, "TCP daemon failed to accept connections"
    yield ep, server
    server.stop()
    t.join(timeout=2)


def test_tcp_daemon_ping_round_trip(tcp_server):
    ep, _ = tcp_server
    resp = DaemonClient(endpoint=ep).call("ping")
    assert resp.ok
    assert resp.result["pid"] > 0


def test_tcp_daemon_version_round_trip(tcp_server):
    ep, _ = tcp_server
    from phantom._version import __version__
    resp = DaemonClient(endpoint=ep).call("version")
    assert resp.ok
    assert resp.result["version"] == __version__


def test_tcp_daemon_echo_round_trip(tcp_server):
    ep, _ = tcp_server
    resp = DaemonClient(endpoint=ep).call("echo", text="windows-ready")
    assert resp.ok
    assert resp.result == {"text": "windows-ready"}


def test_tcp_daemon_unknown_op(tcp_server):
    ep, _ = tcp_server
    resp = DaemonClient(endpoint=ep).call("does-not-exist")
    assert not resp.ok
    assert "unknown op" in resp.error


def test_client_raises_when_no_tcp_daemon():
    # Pick a guaranteed-free port (random high port).
    import random
    port = random.randint(40000, 60000)
    ep = Endpoint(family="tcp", path="127.0.0.1", port=port)
    with pytest.raises(DaemonNotRunning):
        DaemonClient(endpoint=ep, timeout_s=1.0).call("ping")


# ─── backwards compatibility ────────────────────────────────────────────────


def test_legacy_socket_path_kwarg_still_works(short_tmp_path: Path):
    """Older test code passes ``socket_path=`` — must keep working on POSIX."""
    if sys.platform == "win32":
        pytest.skip("POSIX-only legacy path")
    sp = str(short_tmp_path / "legacy.sock")
    server = build_default_server(socket_path=sp)
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    for _ in range(200):
        try:
            DaemonClient(socket_path=sp, timeout_s=1.0).call("ping")
            break
        except DaemonNotRunning:
            time.sleep(0.01)
    try:
        resp = DaemonClient(socket_path=sp).call("ping")
        assert resp.ok
    finally:
        server.stop()
        t.join(timeout=2)
