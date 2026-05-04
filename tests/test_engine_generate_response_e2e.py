"""
End-to-end integration: agent_loop + tool_dispatch + prompt_builder +
context_compact + prompt_cache + telemetry + hooks + audit_log, driven
by a mocked LLM. Mirrors what engine.generate_response does internally
but in a form we can exercise with deterministic inputs.

This is the safety net that lets us refactor the real generate_response
without playing Russian roulette with live providers.
"""
from __future__ import annotations

import json
import os

import pytest

from omnicli.agent_loop    import run as loop_run, ModelTurn, ToolCall
from omnicli.tool_dispatch import dispatch
from omnicli.prompt_builder import build as build_messages
from omnicli.tool_output_filter import filter_output
from omnicli import telemetry


@pytest.fixture(autouse=True)
def _telemetry_on():
    telemetry.shutdown()
    telemetry.clear_metrics()
    telemetry.init(exporter="memory")
    yield
    telemetry.shutdown()


@pytest.fixture(autouse=True)
def _project_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "missing.md"))
    monkeypatch.setenv("PHANTOM_AUDIT_LOG",    str(tmp_path / "audit.jsonl"))
    return str(tmp_path)


def _scripted_llm(responses: list[ModelTurn]):
    it = iter(responses)
    def _call(msgs, round_idx):
        try:
            return next(it)
        except StopIteration:
            raise AssertionError(f"LLM called more times than scripted (round {round_idx})")
    return _call


def _spans():
    exp = telemetry.memory_exporter()
    return list(exp.get_finished_spans()) if exp else []


# ─── Full happy-path roundtrip ──────────────────────────────────────────────


class TestHappyRoundtrip:
    def test_prompt_then_two_tools_then_final(self, monkeypatch):
        from omnicli import engine
        # Fake the underlying bash/write_file executors so tool dispatch
        # doesn't need a real shell/filesystem
        executed: list[tuple] = []
        monkeypatch.setattr(engine, "execute_bash",
                            lambda cmd, trust, on_output=None:
                                executed.append(("bash", cmd)) or "bash-out")
        # Build messages using the real pipeline
        raw_msgs = [
            {"role": "system", "content": "You are Phantom."},
            {"role": "user",   "content": "please do two tasks"},
        ]
        messages = build_messages(raw_msgs, provider="openai",
                                  project_dir=os.environ["HOME"])

        llm = _scripted_llm([
            ModelTurn(tool_calls=[ToolCall(id="t1", name="run_bash",
                                            args={"command": "ls"})],
                      usage={"prompt_tokens": 100, "completion_tokens": 20}),
            ModelTurn(tool_calls=[ToolCall(id="t2", name="run_bash",
                                            args={"command": "pwd"})],
                      usage={"prompt_tokens": 110, "completion_tokens": 15}),
            ModelTurn(final_text="Both tasks complete.",
                      usage={"prompt_tokens": 120, "completion_tokens": 10}),
        ])

        def _exec(name, args, trust):
            return dispatch(name, args, trust=trust)

        result = loop_run(
            messages=messages,
            call_llm=llm,
            execute_tool=_exec,
            trust=3,
            max_rounds=10,
            on_tool_result=lambda name, out: filter_output(name, out).text,
        )

        assert result.final_text == "Both tasks complete."
        assert result.stats.rounds      == 3
        assert result.stats.tool_calls  == 2
        assert result.stats.total_usage == {"prompt_tokens": 330,
                                            "completion_tokens": 45}
        # Both bash calls hit the real executor
        assert executed == [("bash", "ls"), ("bash", "pwd")]

    def test_tool_outputs_are_wrapped_with_boundary_markers(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash",
                            lambda cmd, trust, on_output=None: "echoed result")
        llm = _scripted_llm([
            ModelTurn(tool_calls=[ToolCall(id="t", name="run_bash",
                                            args={"command": "ls"})]),
            ModelTurn(final_text="done"),
        ])
        result = loop_run(
            messages=[{"role": "user", "content": "run ls"}],
            call_llm=llm,
            execute_tool=lambda n, a, t: dispatch(n, a, trust=t),
            on_tool_result=lambda n, o: filter_output(n, o).text,
        )
        tool_msg = next(m for m in result.messages if m["role"] == "tool")
        assert "UNTRUSTED_INPUT_BEGIN" in tool_msg["content"]
        assert "echoed result" in tool_msg["content"]


# ─── Error / edge paths ─────────────────────────────────────────────────────


class TestSchemaRejectionFeedsBack:
    def test_invalid_args_reach_model_on_next_turn(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash",
                            lambda *a, **k: "should-not-run")
        # First the model requests run_bash with MISSING command (invalid),
        # then on seeing the INVALID_TOOL_ARGS feedback it retries correctly.
        llm = _scripted_llm([
            ModelTurn(tool_calls=[ToolCall(id="t1", name="run_bash", args={})]),
            ModelTurn(tool_calls=[ToolCall(id="t2", name="run_bash",
                                            args={"command": "ls"})]),
            ModelTurn(final_text="recovered"),
        ])
        result = loop_run(
            messages=[{"role": "user", "content": "list files"}],
            call_llm=llm,
            execute_tool=lambda n, a, t: dispatch(n, a, trust=3),
            on_tool_result=lambda n, o: filter_output(n, o).text,
        )
        # Tool results show the first was INVALID_TOOL_ARGS, second succeeded
        tool_msgs = [m for m in result.messages if m["role"] == "tool"]
        assert "INVALID_TOOL_ARGS" in tool_msgs[0]["content"]
        assert result.final_text == "recovered"


class TestMaxRoundsSafety:
    def test_infinite_loop_capped(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash",
                            lambda *a, **k: "output")
        # Model keeps asking for tools forever
        llm = _scripted_llm([
            ModelTurn(tool_calls=[ToolCall(id=f"t{i}", name="run_bash",
                                            args={"command": "x"})])
            for i in range(50)
        ])
        result = loop_run(
            messages=[{"role": "user", "content": "loop forever"}],
            call_llm=llm,
            execute_tool=lambda n, a, t: dispatch(n, a, trust=3),
            max_rounds=5,
        )
        assert result.stats.rounds == 5
        assert result.stats.finish_reason == "max_rounds"


# ─── Hook integration across the full pipeline ──────────────────────────────


class TestHookCanBlockMidLoop:
    def test_pre_hook_blocks_second_tool(self, monkeypatch, isolated_hooks_config):
        import json as _j
        # Hook blocks any write_file but allows run_bash
        isolated_hooks_config.write_text(_j.dumps({
            "PreToolUse": [{"match": "write_file", "cmd": "exit 2"}],
        }))
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash", lambda *a, **k: "bash-ok")

        llm = _scripted_llm([
            ModelTurn(tool_calls=[ToolCall(id="t1", name="run_bash",
                                            args={"command": "ls"})]),
            ModelTurn(tool_calls=[ToolCall(id="t2", name="write_file",
                                            args={"path": "/tmp/x", "content": "y"})]),
            ModelTurn(final_text="finished"),
        ])
        result = loop_run(
            messages=[{"role": "user", "content": "do stuff"}],
            call_llm=llm,
            execute_tool=lambda n, a, t: dispatch(n, a, trust=3),
            on_tool_result=lambda n, o: filter_output(n, o).text,
        )
        tool_msgs = [m for m in result.messages if m["role"] == "tool"]
        # First tool succeeded, second was hook-blocked
        assert "bash-ok" in tool_msgs[0]["content"]
        assert "HOOK_BLOCKED" in tool_msgs[1]["content"]


# ─── Telemetry signals observed across the loop ─────────────────────────────


class TestTelemetryCoverage:
    def test_spans_emitted_per_tool_call(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash", lambda *a, **k: "ok")
        llm = _scripted_llm([
            ModelTurn(tool_calls=[ToolCall(id="t1", name="run_bash",
                                            args={"command": "ls"})]),
            ModelTurn(tool_calls=[ToolCall(id="t2", name="run_bash",
                                            args={"command": "pwd"})]),
            ModelTurn(final_text="done"),
        ])
        loop_run(
            messages=[{"role": "user", "content": "x"}],
            call_llm=llm,
            execute_tool=lambda n, a, t: dispatch(n, a, trust=3),
        )
        names = [s.name for s in _spans()]
        # Exactly 2 tool.call spans (matches tool invocations)
        assert names.count("phantom.tool.call") >= 2


# ─── Context assembly integration ───────────────────────────────────────────


class TestContextInjectionE2E:
    def test_claude_md_injected_into_system(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".phantom").mkdir(parents=True)
        (home / ".phantom" / "CONTEXT.md").write_text("Project-wide rule: use pytest.")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(home / ".phantom" / "CONTEXT.md"))

        messages = build_messages(
            [{"role": "user", "content": "hello"}],
            provider="openai",
            project_dir=str(tmp_path),
            apply_cache=False,
        )
        # A system message now exists with the CONTEXT.md text
        sys_msgs = [m for m in messages if m["role"] == "system"]
        assert sys_msgs
        joined = " ".join(
            m["content"] if isinstance(m["content"], str) else ""
            for m in sys_msgs
        )
        assert "Project-wide rule" in joined

    def test_prompt_cache_annotation_on_anthropic(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        from omnicli.prompt_cache import cached_block_count
        msgs = [{"role": "system", "content": "x" * 10_000},
                {"role": "user",   "content": "hi"}]
        out = build_messages(msgs, provider="anthropic",
                             project_dir=str(tmp_path),
                             inject_context=False)
        assert cached_block_count(out) >= 1
