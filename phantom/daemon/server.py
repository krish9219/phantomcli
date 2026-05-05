"""Phantom daemon server — long-lived backend.

Listens on a unix socket on POSIX or a TCP loopback port on Windows.
Each accepted connection is one client request followed by one
response, then close. The expensive imports happen once at server
start; subsequent ``phantom connect`` round-trips are cheap.

Built-in operations
-------------------

* ``ping``        — liveness; returns the daemon's pid + uptime.
* ``version``     — returns ``__version__`` so the client can refuse
                    to talk to a stale daemon after an upgrade.
* ``echo``        — returns ``payload.text`` verbatim.
* ``shutdown``    — graceful stop.

Operators register custom ops via :func:`register_op`.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Callable, Optional

from phantom._version import __version__
from phantom.daemon.protocol import (
    DEFAULT_SOCKET_PATH,
    DaemonRequest,
    DaemonResponse,
    decode_request,
    encode_response,
)
from phantom.daemon.transport import (
    Endpoint,
    default_endpoint,
    is_windows,
    make_client_socket,
    make_listener_socket,
    remove_endpoint_artifacts,
)

__all__ = ["DaemonServer", "build_default_server", "register_op"]

log = logging.getLogger("phantom.daemon.server")

OpHandler = Callable[[dict], dict]


_BUILTIN_OPS: dict[str, OpHandler] = {}


def register_op(name: str, handler: OpHandler) -> None:
    """Register a daemon operation handler.

    The handler receives the request payload dict and returns a result
    dict. Raised exceptions become ``{"ok": false, "error": "..."}``.
    """
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"invalid op name: {name!r}")
    _BUILTIN_OPS[name] = handler


# ─── built-in ops ────────────────────────────────────────────────────────────


_START_TIME = time.monotonic()


def _op_ping(_: dict) -> dict:
    pid = os.getpid()
    return {"pid": pid, "uptime_s": round(time.monotonic() - _START_TIME, 3)}


def _op_version(_: dict) -> dict:
    return {"version": __version__}


def _op_echo(payload: dict) -> dict:
    return {"text": str(payload.get("text", ""))}


register_op("ping", _op_ping)
register_op("version", _op_version)
register_op("echo", _op_echo)


# ─── server ──────────────────────────────────────────────────────────────────


class DaemonServer:
    """Tiny per-line JSON server.

    POSIX: AF_UNIX. Windows: AF_INET on 127.0.0.1. Wire format
    identical. Single-threaded by default; pass ``parallel=True`` to
    dispatch each connection in its own thread.

    Backwards-compatible API: ``socket_path`` still works. Pass
    ``endpoint=`` for explicit transport control.
    """

    def __init__(
        self,
        socket_path: Optional[str] = None,
        *,
        endpoint: Optional[Endpoint] = None,
        parallel: bool = False,
    ) -> None:
        if endpoint is not None:
            self.endpoint = endpoint
        elif socket_path is not None and socket_path != DEFAULT_SOCKET_PATH:
            # Operator-supplied socket path. Wrap in unix endpoint on
            # POSIX. On Windows, treat as a tcp host:port if it looks
            # like one, otherwise still a unix path (Win10+ has AF_UNIX).
            self.endpoint = (default_endpoint(override=socket_path)
                             if (":" in socket_path and not socket_path.startswith("/"))
                             else Endpoint(family="unix", path=socket_path))
        else:
            self.endpoint = default_endpoint()
        # Backwards-compat alias for legacy code that reads .socket_path.
        self.socket_path = self.endpoint.path
        self.parallel = parallel
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        """Bind, listen, and serve forever (until ``stop``)."""
        self._sock = make_listener_socket(self.endpoint)
        log.info("phantom daemon listening on %s", self.endpoint.display())
        try:
            self._accept_loop()
        finally:
            try:
                self._sock.close()
            except Exception:
                pass
            remove_endpoint_artifacts(self.endpoint)

    def stop(self) -> None:
        self._stop.set()
        # Nudge accept() awake by opening + closing a connection.
        try:
            sock = make_client_socket(self.endpoint, timeout_s=1.0)
            sock.close()
        except Exception:
            pass

    def _accept_loop(self) -> None:
        assert self._sock is not None
        self._sock.settimeout(1.0)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if self.parallel:
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
            else:
                self._handle(conn)

    def _handle(self, conn: socket.socket) -> None:
        try:
            with conn:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                    if len(buf) > 1_048_576:
                        conn.sendall(encode_response(DaemonResponse(False, error="request too large")))
                        return
                line, _, _ = buf.partition(b"\n")
                try:
                    req = decode_request(line)
                except (ValueError, json.JSONDecodeError) as e:
                    conn.sendall(encode_response(DaemonResponse(False, error=f"bad request: {e}")))
                    return
                resp = self._dispatch(req)
                conn.sendall(encode_response(resp))
                if req.op == "shutdown" and resp.ok:
                    self.stop()
        except Exception as e:  # pragma: no cover — network errors
            log.exception("daemon handler crashed: %s", e)

    def _dispatch(self, req: DaemonRequest) -> DaemonResponse:
        if req.op == "shutdown":
            return DaemonResponse(True, {"stopping": True})
        handler = _BUILTIN_OPS.get(req.op)
        if handler is None:
            return DaemonResponse(False, error=f"unknown op: {req.op}")
        try:
            return DaemonResponse(True, dict(handler(req.payload)))
        except Exception as e:
            return DaemonResponse(False, error=f"{type(e).__name__}: {e}")


def build_default_server(
    socket_path: Optional[str] = None,
    *,
    endpoint: Optional[Endpoint] = None,
) -> DaemonServer:
    """Construct a server with all default ops wired up.

    With no arguments, picks the OS-appropriate endpoint
    (Unix socket on POSIX, TCP loopback on Windows).
    """
    return DaemonServer(socket_path=socket_path, endpoint=endpoint, parallel=True)
