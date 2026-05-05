"""Cross-platform daemon transport layer.

POSIX
-----

Unix domain socket at :data:`DEFAULT_SOCKET_PATH`. Permission bits
``0o600`` enforce per-user isolation.

Windows
-------

TCP loopback (``127.0.0.1``) on a port derived from the user's SID hash.
Windows 10+ technically supports AF_UNIX but path conventions, ACLs,
and library coverage make TCP loopback the cleaner default. Loopback
is restricted to the local machine by the OS network stack — no
external connections can reach it.

Both backends speak the same newline-delimited JSON wire format.
"""

from __future__ import annotations

import os
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

__all__ = [
    "Endpoint",
    "TransportFamily",
    "default_endpoint",
    "is_windows",
    "make_listener_socket",
    "make_client_socket",
]


TransportFamily = Literal["unix", "tcp"]


def is_windows() -> bool:
    return sys.platform == "win32"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Where the daemon listens / where the client connects.

    For ``family="unix"``, ``path`` is the socket file path and
    ``port`` is unused (kept 0). For ``family="tcp"``, ``path`` is the
    bind host (``127.0.0.1`` by default) and ``port`` is the TCP port.
    """

    family: TransportFamily
    path: str = ""
    port: int = 0

    def display(self) -> str:
        if self.family == "unix":
            return f"unix://{self.path}"
        return f"tcp://{self.path}:{self.port}"


# ─── default endpoint resolution ────────────────────────────────────────────


def _runtime_dir() -> Path:
    if is_windows():
        # On Windows we don't use unix paths, but provide a sane fallback
        # for the rare AF_UNIX path on Win10+ build 17063+.
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "phantom" / "run"
        return Path(os.environ.get("TEMP", "C:\\Windows\\Temp"))
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")


def _user_id_hint() -> str:
    """Stable per-user identifier for endpoint naming.

    POSIX: numeric uid. Windows: hash of the username (no SID lookup
    needed, no extra deps). Process pid is *not* used — the same user
    must reconnect across restarts.
    """
    if is_windows():
        user = os.environ.get("USERNAME", "user")
        # Hash to a 5-digit number deterministic per user.
        h = 0
        for ch in user:
            h = (h * 31 + ord(ch)) & 0xFFFF
        return str(h)
    if hasattr(os, "getuid"):
        return str(os.getuid())
    return "0"


# Loopback port range deliberately above the IANA ephemeral floor (49152)
# but below the typical ephemeral allocation start, so it's free in practice.
_DEFAULT_TCP_BASE_PORT = 17680


def default_endpoint(*, override: Optional[str] = None) -> Endpoint:
    """Return the default endpoint for this OS.

    ``override`` accepts either:
        * a unix socket path (``/tmp/x.sock`` or ``unix:///tmp/x.sock``)
        * a TCP target  (``tcp://127.0.0.1:17680``)
    Otherwise, the OS-appropriate default is returned.
    """
    if override:
        return _parse_endpoint(override)

    if is_windows():
        # Pick a stable per-user port on the loopback range.
        user_offset = int(_user_id_hint()) % 1000
        return Endpoint(family="tcp", path="127.0.0.1",
                        port=_DEFAULT_TCP_BASE_PORT + user_offset)

    # POSIX: unix domain socket under runtime dir.
    runtime = _runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    return Endpoint(family="unix",
                    path=str(runtime / f"phantom-{_user_id_hint()}.sock"))


def _parse_endpoint(spec: str) -> Endpoint:
    s = spec.strip()
    if s.startswith("unix://"):
        return Endpoint(family="unix", path=s[len("unix://"):])
    if s.startswith("tcp://"):
        rest = s[len("tcp://"):]
        if ":" not in rest:
            raise ValueError(f"tcp endpoint missing port: {spec!r}")
        host, port_str = rest.rsplit(":", 1)
        return Endpoint(family="tcp", path=host or "127.0.0.1", port=int(port_str))
    # Bare path → unix socket (POSIX) or TCP if a "host:port" pattern.
    if ":" in s and not s.startswith("/") and not (len(s) > 2 and s[1] == ":"):
        # Looks like "host:port" (and not a Windows path like "C:\...")
        host, _, port_str = s.rpartition(":")
        try:
            return Endpoint(family="tcp", path=host or "127.0.0.1", port=int(port_str))
        except ValueError:
            pass
    return Endpoint(family="unix", path=s)


# ─── socket factories ───────────────────────────────────────────────────────


def make_listener_socket(endpoint: Endpoint) -> socket.socket:
    """Build a listening socket bound to ``endpoint``.

    For ``unix``: removes a stale socket file, binds, sets ``0o600``.
    For ``tcp``: binds to loopback, ``SO_REUSEADDR`` set so quick
    restarts don't get ``EADDRINUSE``.
    """
    if endpoint.family == "unix":
        sp = Path(endpoint.path)
        if sp.exists():
            sp.unlink()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(endpoint.path)
        sock.listen(64)
        try:
            os.chmod(endpoint.path, 0o600)
        except OSError:
            pass
        return sock

    # TCP
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((endpoint.path or "127.0.0.1", endpoint.port))
    sock.listen(64)
    return sock


def make_client_socket(endpoint: Endpoint, *, timeout_s: float = 30.0) -> socket.socket:
    """Build a client socket connected to ``endpoint``.

    Caller owns the resulting socket and is responsible for closing it.
    Raises :class:`ConnectionRefusedError` (and friends) on transport
    failure — the daemon client wraps these.
    """
    if endpoint.family == "unix":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout_s)
        sock.connect(endpoint.path)
        return sock

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    sock.connect((endpoint.path or "127.0.0.1", endpoint.port))
    return sock


def remove_endpoint_artifacts(endpoint: Endpoint) -> None:
    """Best-effort cleanup. Unix sockets leave a file; TCP doesn't."""
    if endpoint.family == "unix":
        try:
            Path(endpoint.path).unlink(missing_ok=True)
        except OSError:
            pass
