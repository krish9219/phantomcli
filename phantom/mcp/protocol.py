"""MCP wire-format types — JSON-RPC 2.0 framing.

The Model Context Protocol is JSON-RPC 2.0 over a transport (stdio is
the default; SSE is also supported by the spec). This module owns the
request/response shapes and the JSON encoding.

We don't pull in a third-party JSON-RPC library — the spec is small
enough that hand-rolling produces clearer error messages and a smaller
dependency footprint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from phantom.errors import ProtocolError

__all__ = [
    "JSONRPC_VERSION",
    "MCPError",
    "MCPRequest",
    "MCPResponse",
    "decode_message",
    "encode_message",
]


JSONRPC_VERSION: str = "2.0"


@dataclass(frozen=True, slots=True)
class MCPRequest:
    """A JSON-RPC 2.0 request."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = None  # ``None`` = notification (no response expected)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": self.method,
        }
        if self.params:
            out["params"] = self.params
        if self.id is not None:
            out["id"] = self.id
        return out


@dataclass(frozen=True, slots=True)
class MCPError:
    """A JSON-RPC 2.0 error object.

    Standard codes:
      -32600 Invalid Request
      -32601 Method not found
      -32602 Invalid params
      -32603 Internal error
      -32000..-32099 Implementation-defined
    """

    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out


@dataclass(frozen=True, slots=True)
class MCPResponse:
    """A JSON-RPC 2.0 response. Exactly one of ``result`` or ``error`` is set."""

    id: int | str | None
    result: Any = None
    error: MCPError | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.error is not None and self.result is not None:
            raise ProtocolError("MCPResponse cannot have both result and error")
        out: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": self.id}
        if self.error is not None:
            out["error"] = self.error.to_dict()
        else:
            out["result"] = self.result
        return out


# ─── codec ────────────────────────────────────────────────────────────────────


def encode_message(msg: MCPRequest | MCPResponse) -> str:
    """Encode *msg* as a single JSON line (no trailing newline)."""
    return json.dumps(msg.to_dict(), separators=(",", ":"))


def decode_message(line: str) -> MCPRequest | MCPResponse:
    """Decode a JSON line into a request or response.

    Raises :class:`ProtocolError` on malformed input.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("JSON-RPC frame must be an object")
    if data.get("jsonrpc") != JSONRPC_VERSION:
        raise ProtocolError(f"jsonrpc field must be {JSONRPC_VERSION!r}")

    if "method" in data:
        method = data["method"]
        if not isinstance(method, str):
            raise ProtocolError("method must be a string")
        params = data.get("params", {})
        if not isinstance(params, dict):
            raise ProtocolError("params must be an object")
        return MCPRequest(method=method, params=params, id=data.get("id"))

    # Response
    if "result" in data:
        return MCPResponse(id=data.get("id"), result=data["result"])
    if "error" in data:
        err = data["error"]
        if not isinstance(err, dict):
            raise ProtocolError("error must be an object")
        return MCPResponse(
            id=data.get("id"),
            error=MCPError(
                code=int(err.get("code", -32603)),
                message=str(err.get("message", "")),
                data=err.get("data"),
            ),
        )
    raise ProtocolError("frame is neither request nor response")
