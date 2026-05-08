"""Tests for `phantom mcp serve` end-to-end.

Drives the stdio MCP server with real tools and asserts it answers
JSON-RPC requests with the expected tool list + behaviour.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from phantom.cli.mcp_serve import build_default_mcp_server, serve_stdio
from phantom.mcp.protocol import (
    JSONRPC_VERSION,
    MCPRequest,
    decode_message,
    encode_message,
)


@pytest.fixture
def server(tmp_path):
    return build_default_mcp_server(workdir=str(tmp_path))


def _drive(server, requests, tmp_path):
    """Pipe *requests* through the stdio loop and return decoded responses."""
    in_buf = io.StringIO("\n".join(encode_message(r) for r in requests) + "\n")
    out_buf = io.StringIO()
    serve_stdio(server, stdin=in_buf, stdout=out_buf)
    out_buf.seek(0)
    return [decode_message(line) for line in out_buf.read().splitlines() if line.strip()]


class TestDefaultServer:
    def test_initialize_then_list_tools(self, server, tmp_path):
        responses = _drive(server, [
            MCPRequest(method="initialize", id=1),
            MCPRequest(method="notifications/initialized", id=None),
            MCPRequest(method="tools/list", id=2),
        ], tmp_path)
        assert len(responses) == 2  # init + tools/list (notification silent)
        init = responses[0]
        assert init.result["serverInfo"]["name"] == "phantom-mcp"
        tools = {t["name"] for t in responses[1].result["tools"]}
        assert tools == {"run_bash", "web_fetch", "read_file",
                          "write_file", "list_dir"}

    def test_list_dir_round_trip(self, server, tmp_path):
        (tmp_path / "alpha.txt").write_text("a")
        (tmp_path / "beta.txt").write_text("b")
        responses = _drive(server, [
            MCPRequest(method="initialize", id=1),
            MCPRequest(method="tools/call", params={
                "name": "list_dir",
                "arguments": {"path": str(tmp_path)},
            }, id=2),
        ], tmp_path)
        result = responses[1].result
        names = {e["name"] for e in result["entries"]}
        assert {"alpha.txt", "beta.txt"} <= names

    def test_write_then_read(self, server, tmp_path):
        target = str(tmp_path / "hello.txt")
        responses = _drive(server, [
            MCPRequest(method="initialize", id=1),
            MCPRequest(method="tools/call", params={
                "name": "write_file",
                "arguments": {"path": target, "text": "world"},
            }, id=2),
            MCPRequest(method="tools/call", params={
                "name": "read_file",
                "arguments": {"path": target},
            }, id=3),
        ], tmp_path)
        assert responses[1].result["ok"]
        assert responses[2].result["text"] == "world"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "GitHub-hosted Windows runners ship Docker Desktop in "
            "Windows-container mode, which rejects the docker backend's "
            "--read-only flag (\"read-only mode is not supported for "
            "Windows containers\"). The MCP run_bash tool is exercised "
            "on Linux+macOS; on Windows users invoke it via the "
            "passthrough sandbox + cmd.exe path covered separately."
        ),
    )
    def test_run_bash_tool_works(self, server, tmp_path):
        responses = _drive(server, [
            MCPRequest(method="initialize", id=1),
            MCPRequest(method="tools/call", params={
                "name": "run_bash",
                "arguments": {"command": "echo hi-from-mcp"},
            }, id=2),
        ], tmp_path)
        result = responses[1].result
        assert result["exit_code"] == 0
        assert "hi-from-mcp" in result["stdout"]

    def test_blank_lines_ignored(self, server, tmp_path):
        in_buf = io.StringIO(
            "\n\n"
            + encode_message(MCPRequest(method="initialize", id=1))
            + "\n"
        )
        out_buf = io.StringIO()
        serve_stdio(server, stdin=in_buf, stdout=out_buf)
        out_buf.seek(0)
        responses = [
            decode_message(line) for line in out_buf.read().splitlines() if line.strip()
        ]
        assert len(responses) == 1
