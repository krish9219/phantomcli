"""Phantom daemon client — thin wrapper over the unix socket.

Used by ``phantom connect`` and by tests. Cold-import cost is dominated
by stdlib ``socket`` + ``json`` — under 50 ms on every Linux box we've
measured.
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator

from phantom.daemon.protocol import (
    DEFAULT_SOCKET_PATH,
    DaemonRequest,
    DaemonResponse,
    decode_response,
    encode_request,
)

__all__ = ["DaemonClient", "DaemonNotRunning", "call"]


class DaemonNotRunning(RuntimeError):
    """Raised when the daemon socket is missing or refuses connection."""


class DaemonClient:
    """Single-shot client. Open, send one request, read one response, close."""

    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH, *, timeout_s: float = 30.0) -> None:
        self.socket_path = socket_path
        self.timeout_s = timeout_s

    def call(self, op: str, **payload) -> DaemonResponse:
        req = DaemonRequest(op=op, payload=dict(payload))
        try:
            with self._open() as sock:
                sock.sendall(encode_request(req))
                buf = b""
                while b"\n" not in buf:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                line, _, _ = buf.partition(b"\n")
                if not line:
                    raise DaemonNotRunning("daemon closed connection without response")
                return decode_response(line)
        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise DaemonNotRunning(f"daemon not running at {self.socket_path}") from e

    @contextmanager
    def _open(self) -> Iterator[socket.socket]:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout_s)
        try:
            s.connect(self.socket_path)
            yield s
        finally:
            try:
                s.close()
            except Exception:
                pass


def call(op: str, *, socket_path: str = DEFAULT_SOCKET_PATH, **payload) -> DaemonResponse:
    """Convenience: one-shot call without building a client."""
    return DaemonClient(socket_path=socket_path).call(op, **payload)
