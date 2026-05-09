"""Tests for the v1.1.12 tool-loop bounds and visibility callbacks.

Triggered by the v1.1.11 user report: kimi-k2.6 went into a 14-minute
silent tool loop. The fixes are:

1. ``max_tool_rounds`` default lowered 25 → 12.
2. ``wall_clock_budget_s`` (default 300s) added; loop bails out
   between rounds when exceeded.
3. ``on_tool_call`` / ``on_tool_result`` callbacks fire once per tool,
   so the chat REPL can print live progress.
"""

from __future__ import annotations

import time
from typing import Any

from phantom.agent import AgentSession, ScriptedProvider, ToolDefinition
from phantom.agent.provider import ProviderResponse, ToolCall


def _identity_tool() -> ToolDefinition:
    """A tool that just echoes its args back as JSON."""
    import json
    return ToolDefinition(
        name="echo",
        description="echo args",
        input_schema={"type": "object"},
        handler=lambda args: json.dumps(args),
    )


def _tool_response(name: str, args: dict, *, text: str = "") -> ProviderResponse:
    return ProviderResponse(
        text=text,
        tool_calls=(ToolCall(id="t1", name=name, arguments=args),),
        finish_reason="tool_calls",
    )


def _final_response(text: str) -> ProviderResponse:
    return ProviderResponse(text=text, tool_calls=(), finish_reason="stop")


# ─── default max_tool_rounds is now 12 ────────────────────────────────────────

def test_default_max_tool_rounds_is_12():
    session = AgentSession(
        provider=ScriptedProvider.from_responses([_final_response("hi")]),
        tools=[_identity_tool()],
    )
    assert session.max_tool_rounds == 12


# ─── tool-round limit returns partial result with marker ─────────────────────

def test_max_tool_rounds_returns_marker(monkeypatch):
    """A model that calls tools forever should bail out at the limit."""
    # 25 tool responses will exceed any reasonable cap.
    responses = [_tool_response("echo", {"i": i}, text=f"step {i}") for i in range(25)]
    session = AgentSession(
        provider=ScriptedProvider.from_responses(responses),
        tools=[_identity_tool()],
        max_tool_rounds=3,
    )
    out = session.respond_to("loop please")
    assert "tool-round limit" in out
    # The model's most recent text should still be in the reply.
    assert "step" in out


# ─── wall-clock budget ──────────────────────────────────────────────────────

def test_wall_clock_budget_bails_out():
    """When the budget is exhausted the loop returns a 'budget exceeded' marker.

    We use a slow tool handler (200ms) and a tight budget (1.05s — just above
    the 1.0s floor). After ~6 rounds the budget trips and the loop returns the
    partial-result marker.
    """
    import json
    slow_tool = ToolDefinition(
        name="echo",
        description="slow echo",
        input_schema={"type": "object"},
        handler=lambda args: time.sleep(0.2) or json.dumps(args),
    )
    # Provide enough tool responses to keep the loop going.
    responses = [_tool_response("echo", {"i": i}, text=f"step {i}") for i in range(20)]
    session = AgentSession(
        provider=ScriptedProvider.from_responses(responses),
        tools=[slow_tool],
        wall_clock_budget_s=1.05,
        max_tool_rounds=20,  # let budget bust first
    )
    out = session.respond_to("loop slowly")
    assert "wall-clock budget" in out


def test_generous_budget_runs_normally():
    session = AgentSession(
        provider=ScriptedProvider.from_responses([_final_response("normal-answer")]),
        tools=[_identity_tool()],
        wall_clock_budget_s=300.0,
    )
    out = session.respond_to("anything")
    assert out == "normal-answer"


# ─── on_tool_call / on_tool_result callbacks ─────────────────────────────────

def test_on_tool_call_fires_for_each_tool():
    captured: list[tuple[int, str, dict]] = []

    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            _tool_response("echo", {"step": 1}),
            _tool_response("echo", {"step": 2}),
            _final_response("done"),
        ]),
        tools=[_identity_tool()],
    )
    session.on_tool_call = lambda r, tc: captured.append((r, tc.name, tc.arguments))
    out = session.respond_to("run twice")
    assert out == "done"
    assert len(captured) == 2
    assert captured[0] == (0, "echo", {"step": 1})
    assert captured[1] == (1, "echo", {"step": 2})


def test_on_tool_result_fires_after_each_tool():
    results: list[str] = []
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            _tool_response("echo", {"x": 9}),
            _final_response("ok"),
        ]),
        tools=[_identity_tool()],
    )
    session.on_tool_result = lambda r, tc, res: results.append(res)
    session.respond_to("run once")
    assert len(results) == 1
    assert '"x": 9' in results[0] or '"x":9' in results[0]


def test_callback_exception_does_not_kill_turn():
    """If the printer crashes (e.g. closed stdout), the agent loop continues."""
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            _tool_response("echo", {}),
            _final_response("survived"),
        ]),
        tools=[_identity_tool()],
    )
    session.on_tool_call = lambda r, tc: 1 / 0
    out = session.respond_to("run")
    assert out == "survived"
