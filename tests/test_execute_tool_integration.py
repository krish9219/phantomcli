"""Integration tests for engine._execute_tool — verifies schema validation
and hooks fire BEFORE the underlying tool, and that PostToolUse fires
after."""
from __future__ import annotations

import json
import sys

import pytest

from omnicli import engine


class TestSchemaRejection:
    def test_missing_required_short_circuits(self, monkeypatch):
        """A call with missing required args must NOT reach execute_bash."""
        called: list[str] = []

        def _boom(*a, **kw):
            called.append("execute_bash_called")
            return "should not happen"

        monkeypatch.setattr(engine, "execute_bash", _boom)
        out = engine._execute_tool("run_bash", {}, trust=3)
        assert called == []
        assert "INVALID_TOOL_ARGS" in out
        assert "run_bash" in out

    def test_non_dict_args_rejected(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(engine, "execute_bash",
                            lambda *a, **k: called.append("x") or "")
        out = engine._execute_tool("run_bash", "not-a-dict", trust=3)  # type: ignore[arg-type]
        assert called == []
        assert "INVALID_TOOL_ARGS" in out

    def test_valid_args_reach_underlying_tool(self, monkeypatch):
        """Happy path — valid args must dispatch to execute_bash."""
        seen = {}

        def _fake_bash(cmd, trust, on_output=None):
            seen["cmd"] = cmd
            seen["trust"] = trust
            return "OK"

        monkeypatch.setattr(engine, "execute_bash", _fake_bash)
        out = engine._execute_tool("run_bash", {"command": "ls"}, trust=3)
        assert out == "OK"
        assert seen == {"cmd": "ls", "trust": 3}


class TestHookBlocking:
    @pytest.mark.skipif(sys.platform == "win32", reason="hook command uses POSIX shell syntax (>&2, ;) that cmd.exe doesn't understand")
    def test_pre_hook_block_short_circuits(self, monkeypatch, isolated_hooks_config):
        """When a PreToolUse hook exits non-zero, the tool must not run."""
        isolated_hooks_config.write_text(json.dumps({
            "PreToolUse": [{"match": "*", "cmd": ">&2 echo DENIED; exit 3"}],
        }))

        called: list[str] = []
        monkeypatch.setattr(engine, "execute_bash",
                            lambda *a, **k: called.append("ran") or "")

        out = engine._execute_tool("run_bash", {"command": "ls"}, trust=3)
        assert called == []
        assert "HOOK_BLOCKED" in out
        assert "run_bash" in out

    def test_post_hook_fires_after_success(self, monkeypatch, isolated_hooks_config, tmp_path):
        """PostToolUse must fire after the tool ran successfully."""
        marker = tmp_path / "post.touched"
        isolated_hooks_config.write_text(json.dumps({
            "PostToolUse": [{"match": "*", "cmd": f"touch {marker}"}],
        }))

        monkeypatch.setattr(engine, "execute_bash", lambda *a, **k: "OUTPUT")

        out = engine._execute_tool("run_bash", {"command": "ls"}, trust=3)
        assert out == "OUTPUT"
        assert marker.is_file()


class TestHookOrdering:
    def test_pre_runs_before_tool_post_runs_after(self, monkeypatch, isolated_hooks_config, tmp_path):
        """Ordering: PreToolUse → tool → PostToolUse."""
        log = tmp_path / "order.log"
        pre = f"echo pre >> {log}"
        post = f"echo post >> {log}"
        isolated_hooks_config.write_text(json.dumps({
            "PreToolUse":  [{"match": "*", "cmd": pre}],
            "PostToolUse": [{"match": "*", "cmd": post}],
        }))

        def _tool(*a, **k):
            with open(log, "a") as f:
                f.write("tool\n")
            return "X"

        monkeypatch.setattr(engine, "execute_bash", _tool)
        engine._execute_tool("run_bash", {"command": "ls"}, trust=3)
        order = [line.strip() for line in log.read_text().splitlines() if line.strip()]
        assert order == ["pre", "tool", "post"]


class TestUnknownToolStillReaches:
    def test_unknown_tool_bypasses_schema(self, monkeypatch):
        """An unknown tool name has no schema — dispatcher should reach the
        'Unknown tool' branch and return that message."""
        out = engine._execute_tool("nonexistent_tool", {"foo": "bar"}, trust=3)
        assert out.startswith("Unknown tool")
