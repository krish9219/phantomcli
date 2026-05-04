"""Tests for :mod:`phantom.mcp.protocol`."""

from __future__ import annotations

import json

import pytest

from phantom.errors import ProtocolError
from phantom.mcp.protocol import (
    JSONRPC_VERSION,
    MCPError,
    MCPRequest,
    MCPResponse,
    decode_message,
    encode_message,
)


class TestEncodeRequest:
    def test_minimal(self):
        out = json.loads(encode_message(MCPRequest(method="ping", id=1)))
        assert out == {"jsonrpc": JSONRPC_VERSION, "method": "ping", "id": 1}

    def test_with_params(self):
        out = json.loads(encode_message(
            MCPRequest(method="ping", params={"x": 1}, id=2),
        ))
        assert out["params"] == {"x": 1}

    def test_notification_omits_id(self):
        out = json.loads(encode_message(
            MCPRequest(method="hi", id=None),
        ))
        assert "id" not in out


class TestEncodeResponse:
    def test_result(self):
        out = json.loads(encode_message(MCPResponse(id=1, result={"ok": True})))
        assert out == {"jsonrpc": JSONRPC_VERSION, "id": 1, "result": {"ok": True}}

    def test_error(self):
        out = json.loads(encode_message(MCPResponse(
            id=1, error=MCPError(code=-32601, message="missing"),
        )))
        assert out["error"] == {"code": -32601, "message": "missing"}
        assert "result" not in out

    def test_cannot_have_both(self):
        with pytest.raises(ProtocolError):
            encode_message(MCPResponse(
                id=1, result={"x": 1}, error=MCPError(code=1, message="x"),
            ))


class TestDecode:
    def test_request(self):
        msg = decode_message('{"jsonrpc":"2.0","method":"ping","id":1}')
        assert isinstance(msg, MCPRequest)
        assert msg.method == "ping" and msg.id == 1

    def test_response_result(self):
        msg = decode_message('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')
        assert isinstance(msg, MCPResponse)
        assert msg.result == {"ok": True}

    def test_response_error(self):
        msg = decode_message(
            '{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"x"}}'
        )
        assert isinstance(msg, MCPResponse)
        assert msg.error and msg.error.code == -32601

    def test_invalid_json(self):
        with pytest.raises(ProtocolError, match="invalid JSON"):
            decode_message("not-json")

    def test_non_object(self):
        with pytest.raises(ProtocolError, match="must be an object"):
            decode_message("[1,2,3]")

    def test_wrong_jsonrpc_version(self):
        with pytest.raises(ProtocolError, match="jsonrpc"):
            decode_message('{"jsonrpc":"1.0","method":"x"}')

    def test_method_must_be_string(self):
        with pytest.raises(ProtocolError, match="method"):
            decode_message('{"jsonrpc":"2.0","method":42}')

    def test_params_must_be_object(self):
        with pytest.raises(ProtocolError, match="params"):
            decode_message('{"jsonrpc":"2.0","method":"x","params":[1,2]}')

    def test_neither_request_nor_response(self):
        with pytest.raises(ProtocolError, match="neither"):
            decode_message('{"jsonrpc":"2.0"}')
