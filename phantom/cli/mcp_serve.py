"""``phantom mcp serve`` — host Phantom's tools as an MCP server over stdio.

Once ``phantom mcp serve`` is running, any MCP client (Claude Desktop,
ChatGPT Desktop, the ``mcp`` CLI, the Cursor MCP integration) can
spawn it and call its tools. The default tool set includes:

* ``run_bash`` — sandboxed shell execution.
* ``web_fetch`` — single-page HTTP GET (no JS, no Chromium).
* ``read_file`` / ``write_file`` / ``list_dir`` — sandboxed file ops.

Operators add the file to their MCP client's config:

.. code-block:: json

    {
      "mcpServers": {
        "phantom": {
          "command": "phantom",
          "args": ["mcp", "serve"]
        }
      }
    }

After that, the AI in their MCP client can run sandboxed commands
through Phantom's executor without the host application needing
sandbox knowledge.

Implementation: this is a thin wrapper around :class:`MCPServer` and
the stdio transport. The server reads from stdin and writes to stdout;
``phantom`` log lines go to stderr (unbuffered) so they don't corrupt
the JSON-RPC frame stream.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from typing import Any

from phantom.engine import ExecuteBashRequest, execute_bash
from phantom.errors import PhantomError
from phantom.mcp import MCPServer, MCPTool

__all__ = ["build_default_mcp_server", "serve_stdio"]

log = logging.getLogger(__name__)


# ─── default tool handlers ──────────────────────────────────────────────────


def _run_bash_handler(workdir: str):
    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        cmd = args.get("command", "")
        if not isinstance(cmd, str) or not cmd.strip():
            raise PhantomError("run_bash: 'command' must be a non-empty string")
        result = execute_bash(ExecuteBashRequest(
            command=cmd,
            workdir=workdir,
            writable_paths=(workdir,),
            network=bool(args.get("network", False)),
        ))
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout[-4096:],
            "stderr": result.stderr[-4096:],
            "tier": result.tier,
            "wall_s": round(result.wall_s, 4),
            "truncated": result.truncated,
        }
    return _handler


def _web_fetch_handler():
    from phantom.tools.web_fetch import web_fetch

    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url", "")
        if not isinstance(url, str) or not url.strip():
            raise PhantomError("web_fetch: 'url' must be a non-empty string")
        max_bytes = int(args.get("max_bytes", 256 * 1024))
        timeout_s = float(args.get("timeout_s", 15.0))
        result = web_fetch(url=url, max_bytes=max_bytes, timeout_s=timeout_s)
        return {
            "ok": result.ok,
            "status": result.status,
            "url": result.url,
            "content_type": result.content_type,
            "text": result.text,
            "truncated": result.truncated,
            "error": result.error,
        }
    return _handler


def _read_file_handler(workdir: str):
    from phantom.tools.fs import read_file

    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path", "")
        if not isinstance(path, str) or not path:
            raise PhantomError("read_file: 'path' is required")
        max_bytes = int(args.get("max_bytes", 256 * 1024))
        return read_file(path=path, allowlist=(workdir,), max_bytes=max_bytes)
    return _handler


def _write_file_handler(workdir: str):
    from phantom.tools.fs import write_file

    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path", "")
        text = args.get("text", "")
        if not isinstance(path, str) or not isinstance(text, str):
            raise PhantomError("write_file: 'path' and 'text' must be strings")
        return write_file(path=path, text=text, allowlist=(workdir,))
    return _handler


def _list_dir_handler(workdir: str):
    from phantom.tools.fs import list_dir

    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path", "")
        if not isinstance(path, str) or not path:
            raise PhantomError("list_dir: 'path' is required")
        return list_dir(path=path, allowlist=(workdir,))
    return _handler


# ─── server factory ──────────────────────────────────────────────────────────


def build_default_mcp_server(*, workdir: str) -> MCPServer:
    """Return an :class:`MCPServer` populated with Phantom's default tools.

    Parameters
    ----------
    workdir:
        Working directory the tools operate in. ``run_bash`` writes
        here; ``read_file`` / ``write_file`` / ``list_dir`` allow only
        paths under here.
    """
    server = MCPServer(name="phantom-mcp", version="4.1.0-dev")

    server.register(MCPTool(
        name="run_bash",
        description=(
            "Execute a shell command in Phantom's sandbox. The command "
            "runs with no network by default. Returns exit_code, stdout, "
            "stderr, tier, wall_s, truncated."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "network": {"type": "boolean", "default": False},
            },
            "required": ["command"],
        },
        handler=_run_bash_handler(workdir),
    ))

    server.register(MCPTool(
        name="web_fetch",
        description=(
            "GET a URL and return its body as text. No JavaScript "
            "execution; for that, use the browser_task tool. Returns "
            "ok, status, url, content_type, text, truncated, error."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_bytes": {"type": "integer", "default": 262144},
                "timeout_s": {"type": "number", "default": 15.0},
            },
            "required": ["url"],
        },
        handler=_web_fetch_handler(),
    ))

    server.register(MCPTool(
        name="read_file",
        description=(
            "Read a file inside the workspace. Refuses paths outside "
            "the configured allow-list. Returns ok, path, text, "
            "size_bytes, truncated, error."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer", "default": 262144},
            },
            "required": ["path"],
        },
        handler=_read_file_handler(workdir),
    ))

    server.register(MCPTool(
        name="write_file",
        description=(
            "Write a UTF-8 file inside the workspace. Refuses paths "
            "outside the configured allow-list. Returns ok, path, "
            "bytes_written, error."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["path", "text"],
        },
        handler=_write_file_handler(workdir),
    ))

    server.register(MCPTool(
        name="list_dir",
        description=(
            "List entries in a directory inside the workspace. Returns "
            "ok, path, entries (each {name, kind, size}), error."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_list_dir_handler(workdir),
    ))

    return server


# ─── stdio loop ──────────────────────────────────────────────────────────────


def serve_stdio(
    server: MCPServer,
    *,
    stdin: io.TextIOBase | None = None,
    stdout: io.TextIOBase | None = None,
) -> None:
    """Read JSON-RPC frames from stdin, dispatch, write responses.

    Tests pass in their own streams; production passes ``sys.stdin``
    and ``sys.stdout``. We intentionally avoid wrapping ``sys.stdin``
    with the buffered text reader because some MCP clients send tightly
    packed frames.
    """
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout

    from phantom.mcp.protocol import (
        MCPRequest,
        decode_message,
        encode_message,
    )

    while True:
        line = in_stream.readline()
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        try:
            msg = decode_message(line)
        except PhantomError as exc:
            log.warning("rejecting malformed frame: %s", exc)
            continue
        if not isinstance(msg, MCPRequest):
            continue
        resp = server.handle(msg)
        if resp is not None:
            out_stream.write(encode_message(resp) + "\n")
            out_stream.flush()
