"""MCP client — connect to a remote MCP server, invoke its tools.

The client speaks JSON-RPC 2.0 over a :class:`Transport`. Real
deployments use a stdio transport (the server is a child process);
tests use an in-memory transport for deterministic assertions.

Lifecycle::

    client = MCPClient(transport)
    client.initialize()
    tools = client.list_tools()
    result = client.call_tool("weather", {"lat": 51.5, "lon": -0.12})
    client.close()
"""

from __future__ import annotations

import itertools
from typing import Any, Protocol, runtime_checkable

from phantom.errors import ProtocolError
from phantom.mcp.protocol import (
    MCPError,
    MCPRequest,
    MCPResponse,
    decode_message,
    encode_message,
)

__all__ = ["MCPClient", "Transport"]


@runtime_checkable
class Transport(Protocol):
    """Abstract bidirectional line-oriented transport."""

    def send_line(self, line: str) -> None: ...
    def recv_line(self) -> str: ...
    def close(self) -> None: ...


class MCPClient:
    """JSON-RPC client speaking the MCP wire format."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._next_id = itertools.count(1)
        self._initialized = False

    # ─── lifecycle ─────────────────────────────────────────────────────

    def initialize(
        self,
        *,
        client_name: str = "phantom",
        client_version: str = "4.0.0-dev",
        protocol_version: str = "2024-11-05",
    ) -> dict[str, Any]:
        """Send an ``initialize`` request and return the server's reply.

        Per the MCP spec, this must be the first message after connect.
        """
        result = self._call("initialize", {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": client_version},
        })
        # Send the ``notifications/initialized`` notification per spec.
        self._notify("notifications/initialized", {})
        self._initialized = True
        return result

    def close(self) -> None:
        try:
            self._transport.close()
        except Exception:
            pass
        self._initialized = False

    # ─── primary calls ─────────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """List tools the server exposes."""
        self._require_init()
        result = self._call("tools/list", {})
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise ProtocolError("tools/list result.tools must be a list")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke tool *name* with *arguments*."""
        self._require_init()
        return self._call("tools/call", {"name": name, "arguments": arguments})

    def list_resources(self) -> list[dict[str, Any]]:
        """List resources the server exposes."""
        self._require_init()
        result = self._call("resources/list", {})
        resources = result.get("resources", [])
        if not isinstance(resources, list):
            raise ProtocolError("resources/list result.resources must be a list")
        return resources

    def read_resource(self, uri: str) -> dict[str, Any]:
        """Read the resource at *uri*."""
        self._require_init()
        return self._call("resources/read", {"uri": uri})

    # ─── primitives ────────────────────────────────────────────────────

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request, wait for the matching response."""
        request_id = next(self._next_id)
        req = MCPRequest(method=method, params=params, id=request_id)
        self._transport.send_line(encode_message(req))
        line = self._transport.recv_line()
        msg = decode_message(line)
        if not isinstance(msg, MCPResponse):
            raise ProtocolError(f"expected response, got {type(msg).__name__}")
        if msg.id != request_id:
            raise ProtocolError(f"response id {msg.id} does not match request id {request_id}")
        if msg.error is not None:
            raise ProtocolError(
                f"server returned error code={msg.error.code} message={msg.error.message!r}"
            )
        if not isinstance(msg.result, dict):
            raise ProtocolError("response.result must be an object")
        return msg.result

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification (no response expected)."""
        req = MCPRequest(method=method, params=params, id=None)
        self._transport.send_line(encode_message(req))

    def _require_init(self) -> None:
        if not self._initialized:
            raise ProtocolError("MCPClient must be initialize()'d before calls")
