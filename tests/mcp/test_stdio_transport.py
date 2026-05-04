"""Tests for :class:`phantom.mcp.transport.StdioTransport`.

These spawn real Python subprocesses that act as MCP servers. The
servers are tiny single-file scripts that read JSON-RPC frames from
stdin and write responses to stdout — exactly the contract a real
MCP server obeys.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from phantom.errors import ProtocolError
from phantom.mcp import MCPClient, StdioTransport
from phantom.mcp.protocol import (
    MCPRequest,
    MCPResponse,
    decode_message,
    encode_message,
)


# A self-contained "echo MCP server" written as a Python script. It
# implements just enough of the spec to round-trip a real client.
ECHO_SERVER_SOURCE = textwrap.dedent("""
    import json, sys

    def respond(req_id, result=None, error=None):
        body = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            body["error"] = error
        else:
            body["result"] = result
        sys.stdout.write(json.dumps(body) + "\\n")
        sys.stdout.flush()

    initialized = False
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "initialize":
            initialized = True
            respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-test", "version": "1.0.0"},
            })
        elif method and method.startswith("notifications/"):
            continue  # silent
        elif not initialized:
            respond(req_id, error={"code": -32002, "message": "not init"})
        elif method == "tools/list":
            respond(req_id, {"tools": [{
                "name": "echo",
                "description": "Echo arguments back.",
                "inputSchema": {"type": "object"},
            }]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            args = params.get("arguments") or {}
            respond(req_id, {"echoed": args})
        else:
            respond(req_id, error={"code": -32601, "message": "method not found"})
""").strip()


@pytest.fixture
def echo_server_script(tmp_path):
    p = tmp_path / "echo_server.py"
    p.write_text(ECHO_SERVER_SOURCE)
    return p


@pytest.fixture
def venv_python():
    # Use the same Python that runs the test suite.
    import sys
    return sys.executable


# ─── basic transport ──────────────────────────────────────────────────────────


class TestStdioTransport:
    def test_spawn_send_recv_close(self, echo_server_script, venv_python):
        with StdioTransport.spawn([venv_python, str(echo_server_script)]) as t:
            t.send_line('{"jsonrpc":"2.0","method":"initialize","id":1,"params":{}}')
            line = t.recv_line()
            data = json.loads(line)
            assert data["id"] == 1
            assert data["result"]["serverInfo"]["name"] == "echo-test"
        # After exit, returncode should be set.
        assert t.returncode is not None

    def test_send_after_close_raises(self, echo_server_script, venv_python):
        t = StdioTransport.spawn([venv_python, str(echo_server_script)])
        t.close()
        with pytest.raises(ProtocolError, match="closed"):
            t.send_line("hi")
        with pytest.raises(ProtocolError, match="closed"):
            t.recv_line()

    def test_close_is_idempotent(self, echo_server_script, venv_python):
        t = StdioTransport.spawn([venv_python, str(echo_server_script)])
        t.close()
        t.close()  # must not raise

    def test_send_line_rejects_embedded_newlines(self, echo_server_script, venv_python):
        with StdioTransport.spawn([venv_python, str(echo_server_script)]) as t:
            with pytest.raises(ProtocolError, match="newline"):
                t.send_line("a\nb")

    def test_empty_argv_rejected(self):
        with pytest.raises(ProtocolError, match="non-empty argv"):
            StdioTransport.spawn([])

    def test_peer_close_surfaces_protocol_error(self, tmp_path, venv_python):
        # A server that exits immediately.
        script = tmp_path / "exit.py"
        script.write_text("import sys; sys.exit(0)")
        with StdioTransport.spawn([venv_python, str(script)]) as t:
            with pytest.raises(ProtocolError, match="peer closed"):
                t.recv_line()


# ─── full client + real server round-trip ────────────────────────────────────


class TestFullRoundTrip:
    def test_real_client_against_real_server(self, echo_server_script, venv_python):
        with StdioTransport.spawn([venv_python, str(echo_server_script)]) as t:
            client = MCPClient(t)
            info = client.initialize()
            assert info["serverInfo"]["name"] == "echo-test"
            tools = client.list_tools()
            assert any(tool["name"] == "echo" for tool in tools)
            result = client.call_tool("echo", {"hello": "world"})
            assert result == {"echoed": {"hello": "world"}}
