"""MCP server — expose Phantom's tools to other agents.

Mirror of :class:`MCPClient`. The server reads JSON-RPC requests from a
transport, dispatches to registered handlers, and writes the responses
back. Tools register as :class:`MCPTool` objects with a JSON-schema for
their input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from phantom.errors import ProtocolError
from phantom.mcp.protocol import (
    MCPError,
    MCPRequest,
    MCPResponse,
    decode_message,
    encode_message,
)

__all__ = ["MCPServer", "MCPTool", "MCPToolHandler", "Transport"]


@runtime_checkable
class Transport(Protocol):
    def send_line(self, line: str) -> None: ...
    def recv_line(self) -> str: ...
    def close(self) -> None: ...


# A handler returns a result dict. It receives the validated arguments.
MCPToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class MCPTool:
    """One tool the server exposes.

    The MCP spec mandates a name + description + input schema.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: MCPToolHandler

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class MCPServer:
    """JSON-RPC MCP server. One per stdio process; reuse across clients."""

    def __init__(self, *, name: str = "phantom-mcp", version: str = "4.0.0-dev") -> None:
        self._name = name
        self._version = version
        self._tools: dict[str, MCPTool] = {}
        self._initialized = False

    # ─── registration ──────────────────────────────────────────────────

    def register(self, tool: MCPTool) -> None:
        if not tool.name:
            raise ProtocolError("MCPTool requires a non-empty name")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    # ─── one-shot dispatch ─────────────────────────────────────────────

    def handle(self, msg: MCPRequest) -> MCPResponse | None:
        """Process a single request; return the response (or None for
        notifications)."""
        method = msg.method

        if method == "initialize":
            self._initialized = True
            return MCPResponse(id=msg.id, result={
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self._name, "version": self._version},
            })

        if method.startswith("notifications/"):
            return None  # silent ack

        if not self._initialized:
            return MCPResponse(id=msg.id, error=MCPError(
                code=-32002, message="server not initialized",
            ))

        if method == "tools/list":
            return MCPResponse(id=msg.id, result={
                "tools": [t.to_dict() for t in self._tools.values()],
            })

        if method == "tools/call":
            tool_name = msg.params.get("name", "")
            args = msg.params.get("arguments", {})
            if tool_name not in self._tools:
                return MCPResponse(id=msg.id, error=MCPError(
                    code=-32601, message=f"unknown tool {tool_name!r}",
                ))
            try:
                result = self._tools[tool_name].handler(args)
            except Exception as exc:
                return MCPResponse(id=msg.id, error=MCPError(
                    code=-32603, message=f"{type(exc).__name__}: {exc}",
                ))
            return MCPResponse(id=msg.id, result=result)

        if method == "resources/list":
            # Stage-4 cut: no resources surfaced by default. Stage-5
            # memory wires the user's memory namespaces here.
            return MCPResponse(id=msg.id, result={"resources": []})

        return MCPResponse(id=msg.id, error=MCPError(
            code=-32601, message=f"method not found: {method}",
        ))

    # ─── transport-driven loop ─────────────────────────────────────────

    def serve_forever(self, transport: Transport) -> None:  # pragma: no cover
        """Read frames forever, dispatch, write responses.

        Used by the bundled stdio entry point (``python -m
        phantom.mcp``). Tests prefer :meth:`handle` directly so they
        don't need to manage a transport.
        """
        try:
            while True:
                line = transport.recv_line()
                if not line.strip():
                    continue
                req = decode_message(line)
                if not isinstance(req, MCPRequest):
                    continue
                resp = self.handle(req)
                if resp is not None:
                    transport.send_line(encode_message(resp))
        finally:
            transport.close()
