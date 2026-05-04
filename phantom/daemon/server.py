"""Phantom daemon server — long-lived backend.

Listens on a unix socket. Each accepted connection is one client request
followed by one response, then close. The expensive imports
(model SDKs, FastAPI, sandbox) happen once at server start; subsequent
``phantom connect`` round-trips are cheap.

Built-in operations
-------------------

* ``ping``        — liveness; returns the daemon's pid + uptime.
* ``version``     — returns ``__version__`` so the client can refuse to
                    talk to a stale daemon after an upgrade.
* ``echo``        — returns ``payload.text`` verbatim. Used by tests.
* ``shutdown``    — graceful stop.

Operators register custom ops via :func:`register_op`. The chat agent
loop is wired in by :func:`build_default_server` once the engine is
ready (Stage 4+); the protocol is stable today regardless.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Callable

from phantom._version import __version__
from phantom.daemon.protocol import (
    DEFAULT_SOCKET_PATH,
    DaemonRequest,
    DaemonResponse,
    decode_request,
    encode_response,
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
    return {"pid": os.getpid(), "uptime_s": round(time.monotonic() - _START_TIME, 3)}


def _op_version(_: dict) -> dict:
    return {"version": __version__}


def _op_echo(payload: dict) -> dict:
    return {"text": str(payload.get("text", ""))}


register_op("ping", _op_ping)
register_op("version", _op_version)
register_op("echo", _op_echo)


# ─── server ──────────────────────────────────────────────────────────────────


class DaemonServer:
    """Tiny per-line JSON server over a unix socket.

    Single-threaded by default — each connection is handled in a daemon
    thread but op handlers run sequentially per connection. Set
    ``parallel=True`` to dispatch each connection in its own thread for
    long-running ops like agent turns.
    """

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET_PATH,
        *,
        parallel: bool = False,
    ) -> None:
        self.socket_path = socket_path
        self.parallel = parallel
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        """Bind, listen, and serve forever (until ``stop``)."""
        sp = Path(self.socket_path)
        if sp.exists():
            sp.unlink()
        sp.parent.mkdir(parents=True, exist_ok=True)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.socket_path)
        self._sock.listen(64)
        os.chmod(self.socket_path, 0o600)
        log.info("phantom daemon listening on %s", self.socket_path)
        try:
            self._accept_loop()
        finally:
            try:
                self._sock.close()
            except Exception:
                pass
            try:
                Path(self.socket_path).unlink(missing_ok=True)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        # nudge accept() awake by opening + closing a connection
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(self.socket_path)
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


def build_default_server(socket_path: str = DEFAULT_SOCKET_PATH) -> DaemonServer:
    """Construct a server with all default ops wired up."""
    return DaemonServer(socket_path=socket_path, parallel=True)
