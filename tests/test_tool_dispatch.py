"""Tests for the tool_dispatch facade."""
from __future__ import annotations

import json

import pytest

from omnicli.tool_dispatch import dispatch


class TestDispatch:
    def test_schema_failure_short_circuits(self, monkeypatch):
        from omnicli import engine
        hits = {"n": 0}
        monkeypatch.setattr(engine, "execute_bash",
                            lambda *a, **k: hits.__setitem__("n", hits["n"] + 1) or "")
        out = dispatch("run_bash", {}, trust=3)
        assert hits["n"] == 0
        assert "INVALID_TOOL_ARGS" in out

    def test_happy_path_reaches_underlying(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash", lambda cmd, trust, on_output=None: f"ran:{cmd}")
        out = dispatch("run_bash", {"command": "ls"}, trust=3)
        assert out == "ran:ls"

    def test_hook_block_short_circuits(self, monkeypatch, isolated_hooks_config):
        from omnicli import engine
        isolated_hooks_config.write_text(json.dumps({
            "PreToolUse": [{"match": "*", "cmd": "exit 2"}],
        }))
        hits = {"n": 0}
        monkeypatch.setattr(engine, "execute_bash",
                            lambda *a, **k: hits.__setitem__("n", hits["n"] + 1) or "")
        out = dispatch("run_bash", {"command": "ls"}, trust=3)
        assert hits["n"] == 0
        assert "HOOK_BLOCKED" in out

    def test_unknown_tool_reaches_unknown_branch(self):
        out = dispatch("future_tool", {"x": 1}, trust=3)
        assert out.startswith("Unknown tool")

    def test_callable_signature(self):
        # dispatch must be a plain callable with the documented signature
        import inspect
        sig = inspect.signature(dispatch)
        params = list(sig.parameters.keys())
        assert params == ["name", "args", "trust", "on_bash_output", "tracker"]
