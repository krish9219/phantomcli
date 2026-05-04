"""Stage 4 smoke test."""

from __future__ import annotations

import pytest

from phantom.acp import AgentRuntime, AgentSpec, AgentStatus
from phantom.mcp import MCPClient, MCPServer, MCPTool
from phantom.mcp.protocol import MCPRequest, decode_message, encode_message


@pytest.mark.stage4
def test_mcp_round_trip_via_in_memory_transport():
    server = MCPServer()
    server.register(MCPTool(
        name="ping", description="ping", input_schema={},
        handler=lambda args: {"pong": True},
    ))
    # Skipping a full transport in the smoke test — directly drive the server.
    server._initialized = True  # noqa: SLF001
    resp = server.handle(MCPRequest(method="tools/call",
                                     params={"name": "ping", "arguments": {}},
                                     id=1))
    assert resp.result == {"pong": True}


@pytest.mark.stage4
def test_acp_runs_dependency_wave():
    seen: list[str] = []

    def body(spec, emit):
        seen.append(spec.agent_id)
        return {}

    rt = AgentRuntime()
    rt.spawn(AgentSpec(agent_id="a", body=body))
    rt.spawn(AgentSpec(agent_id="b", body=body, depends_on=("a",)))
    results = rt.run_all()
    assert seen == ["a", "b"]
    assert all(r.status == AgentStatus.COMPLETED for r in results.values())


@pytest.mark.stage4
def test_phantom_stage_advanced_to_4_or_higher():
    import phantom
    assert phantom.feature_flags()["stage"] >= 4
