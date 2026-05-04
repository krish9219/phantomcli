"""MCP stdio transport — talk to a real MCP server child process.

The MCP spec defines a stdio framing where each JSON-RPC message is
written on its own line, terminated by ``\\n``. This module wraps a
child process spawned via :func:`subprocess.Popen` and exposes the
:class:`Transport` shape :class:`phantom.mcp.client.MCPClient` and
:class:`phantom.mcp.server.MCPServer` consume.

Usage::

    server_argv = ["python", "-m", "my_mcp_server"]
    with StdioTransport.spawn(server_argv) as transport:
        client = MCPClient(transport)
        client.initialize()
        ...

The transport survives across calls; it is closed via the context
manager or :meth:`close`.

This module is one of the **few** places in :mod:`phantom` allowed to
call ``subprocess.*`` outside the sandbox — it's spawning an MCP server
process, which is by definition trusted code the operator already chose
to run. The grep-style test in
``tests/sandbox/test_no_unsandboxed_subprocess.py`` allow-lists this
file explicitly.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from types import TracebackType
from typing import IO, Iterable

from phantom.errors import ProtocolError

__all__ = ["StdioTransport"]


class StdioTransport:
    """Line-framed JSON-RPC transport over a child process's stdio."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        if proc.stdin is None or proc.stdout is None:
            raise ProtocolError("StdioTransport requires Popen with PIPE stdin/stdout")
        self._proc = proc
        self._stdin: IO[bytes] = proc.stdin
        self._stdout: IO[bytes] = proc.stdout
        self._closed = False
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()

    # ─── factory ───────────────────────────────────────────────────────

    @classmethod
    def spawn(
        cls,
        argv: Iterable[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> "StdioTransport":
        """Spawn *argv* as a child process and return a transport.

        stderr is **not** captured — child MCP servers conventionally
        log diagnostics to stderr and the operator wires it to a log
        file. If you need to capture it, replace stderr with a pipe
        before calling this.
        """
        argv_list = list(argv)
        if not argv_list:
            raise ProtocolError("StdioTransport.spawn requires a non-empty argv")
        proc = subprocess.Popen(  # noqa: S603 — explicitly sanctioned per docstring
            argv_list,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,    # we manage flushing
            cwd=str(cwd) if cwd else None,
            env=env if env is not None else os.environ.copy(),
        )
        return cls(proc)

    # ─── line I/O ──────────────────────────────────────────────────────

    def send_line(self, line: str) -> None:
        if self._closed:
            raise ProtocolError("StdioTransport is closed")
        if "\n" in line:
            raise ProtocolError("send_line argument must not contain a newline")
        with self._send_lock:
            try:
                self._stdin.write(line.encode("utf-8"))
                self._stdin.write(b"\n")
                self._stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ProtocolError(f"stdio write failed: {exc}") from exc

    def recv_line(self) -> str:
        if self._closed:
            raise ProtocolError("StdioTransport is closed")
        with self._recv_lock:
            try:
                raw = self._stdout.readline()
            except OSError as exc:
                raise ProtocolError(f"stdio read failed: {exc}") from exc
        if not raw:
            raise ProtocolError("stdio peer closed the stream")
        return raw.decode("utf-8", errors="replace").rstrip("\n")

    # ─── lifecycle ─────────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Try to close stdin politely so the child can exit cleanly.
        try:
            self._stdin.close()
        except Exception:
            pass
        # Then drain stdout so a slow child doesn't leak resources.
        try:
            self._stdout.close()
        except Exception:
            pass
        # Give the child a chance to exit; SIGTERM if it doesn't.
        try:
            self._proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    # ─── context manager ───────────────────────────────────────────────

    def __enter__(self) -> "StdioTransport":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
