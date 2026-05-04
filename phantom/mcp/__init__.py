"""Phantom MCP — Model Context Protocol implementation.

The Model Context Protocol (https://modelcontextprotocol.io) is an
emerging open standard for how AI agents talk to external tool/resource
servers. Phantom v4 ships:

* :class:`MCPClient` — connect to a remote MCP server, list tools,
  invoke them, list resources, read them.
* :class:`MCPServer` — expose Phantom's own tools to other agents.
* JSON-RPC 2.0 framing over stdio.

This module is the wire-format and protocol layer; transport adapters
(stdio, SSE) plug in via the :class:`Transport` Protocol.
"""

from __future__ import annotations

from phantom.mcp.client import MCPClient
from phantom.mcp.protocol import MCPRequest, MCPResponse, MCPError, JSONRPC_VERSION
from phantom.mcp.server import MCPServer, MCPTool, MCPToolHandler
from phantom.mcp.transport import StdioTransport

__all__ = [
    "JSONRPC_VERSION",
    "MCPClient",
    "MCPError",
    "MCPRequest",
    "MCPResponse",
    "MCPServer",
    "MCPTool",
    "MCPToolHandler",
    "StdioTransport",
]
