"""Phantom daemon client — thin wrapper over the daemon transport.

Used by ``phantom connect`` and by tests. Picks AF_UNIX on POSIX or
TCP loopback on Windows automatically. Cold-import cost is stdlib
only (~50 ms on most boxes).
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator, Optional

from phantom.daemon.protocol import (
    DEFAULT_SOCKET_PATH,
    DaemonRequest,
    DaemonResponse,
    decode_response,
    encode_request,
)
from phantom.daemon.transport import (
    Endpoint,
    default_endpoint,
    make_client_socket,
)

__all__ = ["DaemonClient", "DaemonNotRunning", "call"]


class DaemonNotRunning(RuntimeError):
    """Raised when the daemon endpoint is unreachable."""


class DaemonClient:
    """Single-shot client. Open, send one request, read one response, close.

    Backwards-compatible API: ``socket_path`` works exactly as before.
    Pass ``endpoint=`` for explicit transport control.
    """

    def __init__(
        self,
        socket_path: Optional[str] = None,
        *,
        endpoint: Optional[Endpoint] = None,
        timeout_s: float = 30.0,
    ) -> None:
        if endpoint is not None:
            self.endpoint = endpoint
        elif socket_path is not None and socket_path != DEFAULT_SOCKET_PATH:
            self.endpoint = (default_endpoint(override=socket_path)
                             if (":" in socket_path and not socket_path.startswith("/"))
                             else Endpoint(family="unix", path=socket_path))
        else:
            self.endpoint = default_endpoint()
        # Backwards-compat alias used by older test fixtures.
        self.socket_path = self.endpoint.path
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
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            raise DaemonNotRunning(
                f"daemon not running at {self.endpoint.display()}"
            ) from e

    @contextmanager
    def _open(self) -> Iterator[socket.socket]:
        s = make_client_socket(self.endpoint, timeout_s=self.timeout_s)
        try:
            yield s
        finally:
            try:
                s.close()
            except Exception:
                pass


def call(
    op: str,
    *,
    socket_path: Optional[str] = None,
    endpoint: Optional[Endpoint] = None,
    **payload,
) -> DaemonResponse:
    """Convenience: one-shot call without building a client explicitly."""
    return DaemonClient(socket_path=socket_path, endpoint=endpoint).call(op, **payload)
