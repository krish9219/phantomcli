"""End-to-end client + server test using an in-memory transport."""

from __future__ import annotations

from collections import deque

import pytest

from phantom.errors import ProtocolError
from phantom.mcp.client import MCPClient
from phantom.mcp.protocol import decode_message, encode_message, MCPRequest
from phantom.mcp.server import MCPServer, MCPTool


class _BiDirTransport:
    """Two-direction queue. ``client`` sees what server writes and vice-versa."""

    def __init__(self) -> None:
        self.client_out: deque[str] = deque()
        self.server_out: deque[str] = deque()


class _ClientView:
    def __init__(self, t: _BiDirTransport, server: MCPServer):
        self._t = t
        self._server = server

    def send_line(self, line: str) -> None:
        self._t.client_out.append(line)
        # Synchronously dispatch to the server.
        req = decode_message(line)
        if isinstance(req, MCPRequest):
            resp = self._server.handle(req)
            if resp is not None:
                self._t.server_out.append(encode_message(resp))

    def recv_line(self) -> str:
        return self._t.server_out.popleft()

    def close(self) -> None:
        pass


@pytest.fixture
def server_and_client():
    server = MCPServer()
    server.register(MCPTool(
        name="echo",
        description="Return the input.",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=lambda args: {"echoed": args.get("text", "")},
    ))
    server.register(MCPTool(
        name="boom",
        description="Always raises.",
        input_schema={"type": "object"},
        handler=lambda args: (_ for _ in ()).throw(ValueError("nope")),
    ))
    transport = _BiDirTransport()
    client = MCPClient(_ClientView(transport, server))
    return server, client


class TestRoundTrip:
    def test_initialize(self, server_and_client):
        _, client = server_and_client
        info = client.initialize()
        assert info["protocolVersion"] == "2024-11-05"
        assert info["serverInfo"]["name"] == "phantom-mcp"

    def test_list_tools(self, server_and_client):
        _, client = server_and_client
        client.initialize()
        tools = client.list_tools()
        names = {t["name"] for t in tools}
        assert names == {"echo", "boom"}

    def test_call_tool(self, server_and_client):
        _, client = server_and_client
        client.initialize()
        out = client.call_tool("echo", {"text": "hello"})
        assert out == {"echoed": "hello"}

    def test_call_unknown_tool(self, server_and_client):
        _, client = server_and_client
        client.initialize()
        with pytest.raises(ProtocolError, match="unknown tool"):
            client.call_tool("missing", {})

    def test_call_tool_handler_error_surfaces(self, server_and_client):
        _, client = server_and_client
        client.initialize()
        with pytest.raises(ProtocolError, match="ValueError"):
            client.call_tool("boom", {})

    def test_call_before_init_raises(self, server_and_client):
        _, client = server_and_client
        with pytest.raises(ProtocolError, match="initialize"):
            client.list_tools()

    def test_list_resources_default_empty(self, server_and_client):
        _, client = server_and_client
        client.initialize()
        assert client.list_resources() == []


class TestServerDispatch:
    def test_method_not_found(self):
        server = MCPServer()
        server._initialized = True  # noqa: SLF001 — direct dispatch test
        resp = server.handle(MCPRequest(method="bogus", id=99))
        assert resp.error and resp.error.code == -32601

    def test_uninitialized_calls_rejected(self):
        server = MCPServer()
        resp = server.handle(MCPRequest(method="tools/list", id=1))
        assert resp.error and resp.error.code == -32002

    def test_register_unregister_tools(self):
        server = MCPServer()
        server.register(MCPTool(
            name="x", description="x", input_schema={},
            handler=lambda a: {"ok": True},
        ))
        assert "x" in server.tool_names()
        server.unregister("x")
        assert "x" not in server.tool_names()

    def test_register_empty_name_rejected(self):
        server = MCPServer()
        with pytest.raises(ProtocolError, match="non-empty name"):
            server.register(MCPTool(
                name="", description="x", input_schema={}, handler=lambda a: {},
            ))

    def test_notifications_silent(self):
        server = MCPServer()
        resp = server.handle(MCPRequest(method="notifications/initialized", id=None))
        assert resp is None
