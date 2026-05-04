"""Tests for the extracted agent_loop."""
from __future__ import annotations

import pytest

from omnicli.agent_loop import run, ModelTurn, ToolCall


def _make_llm(responses):
    """Return a call_llm fn that yields scripted responses in order."""
    it = iter(responses)
    def _call(msgs, round_idx):
        try:
            return next(it)
        except StopIteration:
            raise AssertionError("LLM called more times than scripted")
    return _call


class TestHappyPath:
    def test_final_text_on_first_turn(self):
        def _tool(*a, **k): raise AssertionError("tool should not be called")
        llm = _make_llm([ModelTurn(final_text="done")])
        r = run([{"role": "user", "content": "hi"}], llm, _tool)
        assert r.final_text == "done"
        assert r.stats.rounds == 1
        assert r.stats.model_calls == 1
        assert r.stats.tool_calls == 0
        assert r.stats.finished is True
        assert r.stats.finish_reason == "final_text"

    def test_one_tool_then_final(self):
        tool_hits = []
        def _tool(name, args, trust):
            tool_hits.append((name, args))
            return "tool-output"
        llm = _make_llm([
            ModelTurn(tool_calls=[ToolCall(id="c1", name="run_bash",
                                            args={"command": "ls"})]),
            ModelTurn(final_text="all done"),
        ])
        r = run([{"role": "user", "content": "hi"}], llm, _tool)
        assert r.final_text == "all done"
        assert r.stats.rounds == 2
        assert r.stats.tool_calls == 1
        assert tool_hits == [("run_bash", {"command": "ls"})]
        # Messages contain: initial user + assistant-with-tool-calls + tool result + assistant-final
        roles = [m["role"] for m in r.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_multiple_tools_in_one_turn(self):
        def _tool(name, args, trust):
            return f"ran {name}"
        llm = _make_llm([
            ModelTurn(tool_calls=[
                ToolCall(id="c1", name="run_bash",   args={"command": "pwd"}),
                ToolCall(id="c2", name="read_file",  args={"path": "/tmp/x"}),
            ]),
            ModelTurn(final_text="combined result"),
        ])
        r = run([{"role": "user", "content": "hi"}], llm, _tool)
        assert r.stats.tool_calls == 2
        assert r.stats.rounds == 2
        # Two tool-role messages should be appended
        tool_msgs = [m for m in r.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["content"] == "ran run_bash"
        assert tool_msgs[1]["content"] == "ran read_file"


class TestMaxRounds:
    def test_cap_hit_returns_sentinel(self):
        # Model keeps asking for a tool forever — loop caps out
        def _tool(*a, **k): return "ok"
        llm = _make_llm([
            ModelTurn(tool_calls=[ToolCall(id=f"c{i}", name="x", args={})])
            for i in range(10)
        ])
        r = run([{"role": "user", "content": "?"}], llm, _tool, max_rounds=3)
        assert r.stats.rounds == 3
        assert r.stats.finish_reason == "max_rounds"
        assert "max_rounds" in r.final_text or "exceeded" in r.final_text

    def test_max_rounds_floor_at_one(self):
        llm = _make_llm([ModelTurn(final_text="ok")])
        r = run([], llm, lambda *a, **k: "", max_rounds=0)
        # Floor clamps to 1 → still succeeds on round 0
        assert r.stats.rounds == 1


class TestEmptyTurn:
    def test_model_returns_neither_text_nor_tools(self):
        llm = _make_llm([ModelTurn()])
        r = run([], llm, lambda *a, **k: "")
        assert r.stats.finished is True
        assert r.stats.finish_reason == "empty_turn"


class TestUsageTracking:
    def test_usage_summed_across_rounds(self):
        def _tool(*a, **k): return "ok"
        llm = _make_llm([
            ModelTurn(tool_calls=[ToolCall(id="c", name="x", args={})],
                      usage={"prompt_tokens": 100, "completion_tokens": 20}),
            ModelTurn(final_text="done",
                      usage={"prompt_tokens": 150, "completion_tokens": 30}),
        ])
        r = run([], llm, _tool)
        assert r.stats.total_usage["prompt_tokens"]     == 250
        assert r.stats.total_usage["completion_tokens"] == 50


class TestOutputFilter:
    def test_on_tool_result_wraps(self):
        captured = []
        def _filter(name, out):
            captured.append((name, out))
            return f"[WRAPPED:{name}] {out}"
        def _tool(name, args, trust): return "raw"
        llm = _make_llm([
            ModelTurn(tool_calls=[ToolCall(id="c", name="run_bash", args={})]),
            ModelTurn(final_text="ok"),
        ])
        r = run([], llm, _tool, on_tool_result=_filter)
        assert captured == [("run_bash", "raw")]
        tool_msg = next(m for m in r.messages if m["role"] == "tool")
        assert tool_msg["content"] == "[WRAPPED:run_bash] raw"


class TestObservers:
    def test_round_observers_fire(self):
        starts, ends = [], []
        def _tool(*a, **k): return "ok"
        llm = _make_llm([
            ModelTurn(tool_calls=[ToolCall(id="c", name="x", args={})]),
            ModelTurn(final_text="done"),
        ])
        r = run([], llm, _tool,
                on_round_start=lambda i, info: starts.append(i),
                on_round_end=lambda i, info: ends.append(i))
        assert starts == [0, 1]
        assert ends == [0, 1]

    def test_broken_observer_is_swallowed(self):
        def _bad(i, info): raise RuntimeError("observer boom")
        def _tool(*a, **k): return "ok"
        llm = _make_llm([ModelTurn(final_text="fine")])
        # Must not raise
        r = run([], llm, _tool,
                on_round_start=_bad, on_round_end=_bad)
        assert r.final_text == "fine"


class TestMessageShape:
    def test_tool_call_message_has_openai_shape(self):
        def _tool(*a, **k): return "out"
        llm = _make_llm([
            ModelTurn(tool_calls=[ToolCall(id="xyz", name="run_bash",
                                            args={"command": "ls -la"})]),
            ModelTurn(final_text="ok"),
        ])
        r = run([], llm, _tool)
        assistant_with_tool = r.messages[0]
        assert assistant_with_tool["role"] == "assistant"
        assert assistant_with_tool["tool_calls"][0]["id"] == "xyz"
        assert assistant_with_tool["tool_calls"][0]["type"] == "function"
        assert assistant_with_tool["tool_calls"][0]["function"]["name"] == "run_bash"
        # Arguments are JSON-stringified
        import json
        args = json.loads(assistant_with_tool["tool_calls"][0]["function"]["arguments"])
        assert args == {"command": "ls -la"}

    def test_tool_result_has_required_fields(self):
        def _tool(*a, **k): return "RESULT"
        llm = _make_llm([
            ModelTurn(tool_calls=[ToolCall(id="abc", name="read_file", args={})]),
            ModelTurn(final_text="ok"),
        ])
        r = run([], llm, _tool)
        tool_msg = next(m for m in r.messages if m["role"] == "tool")
        assert tool_msg["tool_call_id"] == "abc"
        assert tool_msg["name"] == "read_file"
        assert tool_msg["content"] == "RESULT"


class TestCallerListImmutability:
    def test_caller_messages_not_mutated(self):
        original = [{"role": "user", "content": "hi"}]
        snapshot = list(original)
        def _tool(*a, **k): return ""
        llm = _make_llm([ModelTurn(final_text="ok")])
        run(original, llm, _tool)
        assert original == snapshot   # unchanged
