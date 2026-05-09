"""Tests for v1.1.23 — round-cap raise, repeat-loop detector, identity
hammer.

Triggered by the v1.1.22 user 10-prompt regression where 7 of 10 hit
the 12-round limit while doing legitimate multi-step work (build a
9-file FastAPI project + tests + run server takes ~15 rounds). The
old solution was hard-clip; the new one is hard-clip at 25 PLUS a
real-loop detector that catches "same tool, same args, three in a
row" — the actual infinite-loop signature.
"""

from __future__ import annotations

import json
from typing import Iterable

import pytest

from phantom.agent import AgentSession, ScriptedProvider, ToolDefinition
from phantom.agent.provider import ProviderResponse, ToolCall


def _identity_tool() -> ToolDefinition:
    return ToolDefinition(
        name="echo",
        description="echo args",
        input_schema={"type": "object"},
        handler=lambda args: json.dumps(args),
    )


def _tool_resp(name: str, args: dict, *, text: str = "") -> ProviderResponse:
    return ProviderResponse(
        text=text,
        tool_calls=(ToolCall(id="t1", name=name, arguments=args),),
        finish_reason="tool_calls",
    )


def _final(text: str) -> ProviderResponse:
    return ProviderResponse(text=text, tool_calls=(), finish_reason="stop")


# ─── Round cap raised to 25 ──────────────────────────────────────────────────

def test_default_max_tool_rounds_is_25():
    """v1.1.23: raised from 12 because legitimate multi-file projects
    routinely need 15-20 rounds."""
    session = AgentSession(
        provider=ScriptedProvider.from_responses([_final("ok")]),
    )
    assert session.max_tool_rounds == 25


def test_round_cap_still_bails_at_limit():
    """A model that calls genuinely-different tools forever still hits
    the cap — the new max is 25 not infinity."""
    # 30 unique-arg tool calls to overshoot the cap.
    responses = [_tool_resp("echo", {"i": i}, text=f"step {i}") for i in range(30)]
    session = AgentSession(
        provider=ScriptedProvider.from_responses(responses),
        tools=[_identity_tool()],
        max_tool_rounds=4,  # tight cap for this test
    )
    out = session.respond_to("loop")
    assert "tool-round limit" in out


# ─── Repeat-args loop detector ───────────────────────────────────────────────

def test_three_identical_calls_in_a_row_aborts_with_marker():
    """Same tool + same args 3x in a row = real loop. Catch and bail."""
    same = _tool_resp("echo", {"x": 1}, text="trying again")
    responses = [same, same, same, _final("never reached")]
    session = AgentSession(
        provider=ScriptedProvider.from_responses(responses),
        tools=[_identity_tool()],
        max_tool_rounds=25,
    )
    out = session.respond_to("loop")
    assert "infinite loop" in out
    assert "echo" in out  # tool name surfaced in the message
    assert "/reset" in out


def test_two_identical_calls_then_different_does_not_trigger():
    """Two repeats followed by a different call is normal retry behaviour
    (e.g. fixing a bash command after a typo). Must NOT bail."""
    a = _tool_resp("echo", {"x": 1})
    b = _tool_resp("echo", {"x": 2})
    responses = [a, a, b, _final("done")]
    session = AgentSession(
        provider=ScriptedProvider.from_responses(responses),
        tools=[_identity_tool()],
        max_tool_rounds=25,
    )
    out = session.respond_to("retry")
    assert out == "done"
    assert "infinite loop" not in out


def test_three_calls_different_args_does_not_trigger():
    """Three calls with different args is the legitimate file-by-file
    workflow (write_file path=a.py, write_file path=b.py, ...)."""
    responses = [
        _tool_resp("echo", {"path": f"f{i}.py"}, text=f"writing f{i}.py")
        for i in range(3)
    ]
    responses.append(_final("done"))
    session = AgentSession(
        provider=ScriptedProvider.from_responses(responses),
        tools=[_identity_tool()],
        max_tool_rounds=25,
    )
    out = session.respond_to("write 3 files")
    assert out == "done"
    assert "infinite loop" not in out


def test_three_different_tools_each_with_different_args():
    """write_file → run_bash → start_server is the canonical 'create + run'
    pattern. Even three rounds in a row is normal."""
    bash_tool = ToolDefinition(
        name="run_bash", description="x",
        input_schema={"type": "object"},
        handler=lambda a: json.dumps({"ok": True}),
    )
    server_tool = ToolDefinition(
        name="start_server", description="x",
        input_schema={"type": "object"},
        handler=lambda a: json.dumps({"url": "http://x"}),
    )
    responses = [
        _tool_resp("echo", {"path": "app.py"}),
        _tool_resp("run_bash", {"command": "pip install flask"}),
        _tool_resp("start_server", {"command": "python app.py"}),
        _final("server up"),
    ]
    session = AgentSession(
        provider=ScriptedProvider.from_responses(responses),
        tools=[_identity_tool(), bash_tool, server_tool],
        max_tool_rounds=25,
    )
    out = session.respond_to("build + run")
    assert out == "server up"


# ─── Identity hammer (second system message before each user turn) ──────────

def test_identity_hint_inserted_before_user_message_when_set():
    """Verify the agent loop prepends a second system message with the
    identity hint right before the user's turn — when set."""
    captured_messages: list = []

    class _Capturer:
        name = "scripted"
        def complete(self, messages, *, tools):
            captured_messages.append(list(messages))
            return _final("ok")

    session = AgentSession(provider=_Capturer())
    session._phantom_identity_hint = "REMINDER: Your name is Ghost."
    session.respond_to("hello")

    msgs = captured_messages[0]
    # Two system messages now: original + reminder.
    system_idxs = [i for i, m in enumerate(msgs) if m.role == "system"]
    assert len(system_idxs) == 2
    # Reminder is the second system message and sits IMMEDIATELY before
    # the user message.
    assert "Ghost" in msgs[system_idxs[1]].content
    assert msgs[system_idxs[1] + 1].role == "user"


def test_identity_hint_unset_keeps_single_system_message():
    """No hint set → no extra system message (no behaviour change)."""
    captured_messages: list = []

    class _Capturer:
        name = "scripted"
        def complete(self, messages, *, tools):
            captured_messages.append(list(messages))
            return _final("ok")

    session = AgentSession(provider=_Capturer())
    session.respond_to("hello")
    msgs = captured_messages[0]
    system_idxs = [i for i, m in enumerate(msgs) if m.role == "system"]
    assert len(system_idxs) == 1


def test_identity_hint_does_not_break_tool_loop():
    """Multi-round tool-calling turn with a hint set: hint appears in
    EVERY provider call, not just the first."""
    captured = []

    class _Capturer:
        name = "scripted"
        _replies = [
            _tool_resp("echo", {"i": 1}),
            _tool_resp("echo", {"i": 2}),
            _final("done"),
        ]
        def complete(self, messages, *, tools):
            captured.append([m.role for m in messages])
            return self._replies.pop(0)

    session = AgentSession(provider=_Capturer(), tools=[_identity_tool()])
    session._phantom_identity_hint = "REMINDER: Your name is Ghost."
    out = session.respond_to("hi")
    assert out == "done"
    # Hint shows up in EACH round (not just the first).
    for roles in captured:
        # Each call starts with system, …, system (hint), user.
        assert roles.count("system") == 2
