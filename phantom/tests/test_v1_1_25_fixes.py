"""Tests for v1.1.25 — auto-continue mid-task checkpoints + _looks_like_premature_checkpoint.

Triggered by the v1.1.24 user run: model kept saying things like
"Re-running pytest now." / "Now I'll fix the schema." / "Installing
pytest-asyncio." and stopping, forcing the user to type "yeah proceed"
between every step. The agent loop now detects these futures-tense
checkpoint phrases (when used WITH prior tool calls in the turn) and
auto-injects a continuation user message instead of returning.
"""

from __future__ import annotations

import json

import pytest

from phantom.agent import AgentSession, ScriptedProvider, ToolDefinition
from phantom.agent.provider import ProviderResponse, ToolCall
from phantom.agent.session import (
    _looks_like_premature_checkpoint,
    DEFAULT_SYSTEM_PROMPT,
)


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


# ─── _looks_like_premature_checkpoint heuristic ──────────────────────────────

@pytest.mark.parametrize("text", [
    # The exact phrases the user observed in the v1.1.24 trace.
    "Re-running pytest now.",
    "Now I'll fix the schema.",
    "Need pytest-asyncio for async tests. Installing and adding markers.",
    "Let me start the server.",
    "Installing pytest-asyncio.",
    "I'll run pytest next.",
    "Now starting the server.",
    "Let me check the file and try again.",
    "I'll edit it and re-run.",
    "Now I'll verify the fix.",
])
def test_premature_checkpoint_phrases_detected(text):
    assert _looks_like_premature_checkpoint(text) is True


@pytest.mark.parametrize("text", [
    # Real final-state summaries — must NOT trigger.
    "Server up at http://127.0.0.1:8000/docs. 9/9 tests pass.",
    "Bug fixed (was `+ 1` instead of `- 1`); all 4 tests green.",
    "Created 9 files; pytest 14/14 ✓; live at http://127.0.0.1:5000.",
    "Done. Tasks 1, 2, 3 added; task 1 marked complete.",
    # Conversational answers (not mid-task).
    "I'm Ghost — a coding agent that runs on a configurable model.",
    "Hello! How can I help you today?",
])
def test_non_checkpoint_phrases_not_flagged(text):
    assert _looks_like_premature_checkpoint(text) is False


def test_long_final_summary_not_flagged():
    """A 500-char final report mentioning 'I'll' incidentally must NOT
    trigger — only short forward-looking-promise messages do."""
    text = (
        "Done. The flask-tz refactor is complete. I extracted the timezone "
        "list into a new clocks.py module with full type annotations and "
        "docstrings. app.py was updated to import ZONES from clocks. The "
        "server restarted cleanly on port 5001 and /api/time returns all "
        "four zones (UTC, IST, UK, NY). I'll mention that in your next "
        "PR review checklist when we get there."
    )
    assert _looks_like_premature_checkpoint(text) is False


def test_empty_or_short_text_not_flagged():
    assert _looks_like_premature_checkpoint("") is False
    assert _looks_like_premature_checkpoint("ok") is False
    assert _looks_like_premature_checkpoint("done.") is False


# ─── Auto-continue end-to-end through the agent loop ────────────────────────

def test_auto_continue_kicks_in_after_checkpoint_with_prior_tools():
    """Sequence: model calls a tool → returns text 'Re-running pytest now.'
    → agent should NOT return to user; instead inject continuation user
    message and call provider again. Provider then calls another tool and
    returns the real final summary."""
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            _tool_resp("echo", {"step": 1}),
            _final("Re-running pytest now."),       # ← premature
            _tool_resp("echo", {"step": 2}),         # auto-continue → tool
            _final("Done. 9/9 tests pass at http://127.0.0.1:5000."),
        ]),
        tools=[_identity_tool()],
    )
    out = session.respond_to("build and run")
    assert "9/9 tests pass" in out  # final summary, not the checkpoint


def test_auto_continue_skipped_when_no_prior_tools():
    """If the very FIRST response is a 'Let me X' — no tools yet — the
    user gets the message as-is. We only auto-continue when the model
    already started a multi-tool task."""
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            _final("Let me think about that."),
        ]),
        tools=[_identity_tool()],
    )
    out = session.respond_to("hi")
    assert out == "Let me think about that."


def test_auto_continue_capped_at_three():
    """The model could in principle keep checkpointing forever. We cap
    auto-continues at 3 per turn so a misbehaving model can't burn
    unlimited rounds."""
    # Pattern: tool, "I'll continue", tool, "Now I'll continue",
    # tool, "Let me continue", tool, "I'll continue" → after 3 auto-
    # continues we surface the next final-text-with-promise to the user.
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            _tool_resp("echo", {"i": 1}),
            _final("I'll continue."),               # auto-continue 1
            _tool_resp("echo", {"i": 2}),
            _final("Now I'll continue."),           # auto-continue 2
            _tool_resp("echo", {"i": 3}),
            _final("Let me continue."),             # auto-continue 3
            _tool_resp("echo", {"i": 4}),
            _final("I'll keep going."),             # 4th: NOT auto-continued
        ]),
        tools=[_identity_tool()],
        max_tool_rounds=25,
    )
    out = session.respond_to("loop")
    # The 4th promise gets surfaced to the user — the cap held.
    assert out == "I'll keep going."


def test_legitimate_completion_not_auto_continued():
    """Final summary that doesn't read like a promise must pass straight
    through, even if there were tool calls in the turn."""
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            _tool_resp("echo", {"step": 1}),
            _final("Done. Server up at http://127.0.0.1:5000. 14/14 tests passing."),
        ]),
        tools=[_identity_tool()],
    )
    out = session.respond_to("build")
    assert "Done" in out
    assert "14/14" in out


# ─── Default system prompt now hammers anti-checkpointing ───────────────────

def test_default_prompt_warns_against_premature_stops():
    low = DEFAULT_SYSTEM_PROMPT.lower()
    assert "do not stop mid-task" in low
    assert "yeah proceed" in low  # the exact pattern the user observed
    # Specific examples are listed (the model is more likely to follow
    # patterns it has seen demonstrated).
    assert "re-running pytest now" in low
    assert "let me start the server" in low
