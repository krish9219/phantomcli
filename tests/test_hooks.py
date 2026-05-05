"""Tests for the PreToolUse / PostToolUse hook system."""
from __future__ import annotations

import json
import os
import sys

import pytest

from omnicli.hooks import dispatch, is_configured, HookResult


def _write_config(path, data):
    path.write_text(json.dumps(data))


class TestNoConfig:
    def test_no_config_is_not_configured(self, isolated_hooks_config):
        # Fixture sets PHANTOM_HOOKS_CONFIG but doesn't create the file.
        assert is_configured() is False

    def test_no_config_dispatch_is_allowed(self, isolated_hooks_config):
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {"command": "ls"}})
        assert r.allowed is True


class TestPreToolUseAllow:
    def test_exit_zero_allows(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreToolUse": [{"match": "*", "cmd": "exit 0"}],
        })
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is True
        assert r.exit_code == 0

    def test_tool_name_mismatch_skips_hook(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreToolUse": [{"match": "run_bash", "cmd": "exit 1"}],
        })
        # Hook matches only run_bash — a write_file call should pass.
        r = dispatch("PreToolUse", {"tool": "write_file", "args": {}})
        assert r.allowed is True


class TestPreToolUseBlock:
    def test_nonzero_exit_blocks(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreToolUse": [{"match": "*", "cmd": "exit 2"}],
        })
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is False
        assert r.exit_code == 2
        assert "exit 2" in r.reason

    @pytest.mark.skipif(sys.platform == "win32", reason="hook command uses POSIX shell syntax (>&2, ;) that cmd.exe doesn't understand")
    def test_stderr_captured(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreToolUse": [{"match": "*", "cmd": ">&2 echo BLOCKED; exit 7"}],
        })
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is False
        assert "BLOCKED" in r.stderr

    def test_matched_tool_blocks(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreToolUse": [
                {"match": "run_bash", "cmd": "exit 1"},
                {"match": "write_file", "cmd": "exit 0"},
            ],
        })
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is False

    def test_first_blocker_wins(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreToolUse": [
                {"match": "*", "cmd": "exit 0"},
                {"match": "*", "cmd": "exit 9"},  # blocker
                {"match": "*", "cmd": "exit 0"},
            ],
        })
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is False
        assert r.exit_code == 9


class TestPayloadDelivery:
    def test_stdin_receives_json_payload(self, isolated_hooks_config, tmp_path):
        # Hook reads stdin and writes it to a file; we then assert its contents.
        dump_file = tmp_path / "hook_stdin.json"
        _write_config(isolated_hooks_config, {
            "PreToolUse": [{"match": "*", "cmd": f"cat > {dump_file}"}],
        })
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {"command": "ls"}})
        assert r.allowed is True
        assert dump_file.is_file()
        payload = json.loads(dump_file.read_text())
        assert payload["tool"] == "run_bash"
        assert payload["args"]["command"] == "ls"


class TestTimeout:
    def test_timeout_blocks(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreToolUse": [{"match": "*", "cmd": "sleep 30", "timeout": 1}],
        })
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is False
        assert "timed out" in r.reason.lower()


class TestPostToolUse:
    def test_post_fires_but_non_blocking_contract(self, isolated_hooks_config, tmp_path):
        # PostToolUse should still run the hook and capture its result, but
        # callers are expected to ignore the allowed=False flag. We only
        # verify the hook executed at all.
        marker = tmp_path / "post_fired"
        _write_config(isolated_hooks_config, {
            "PostToolUse": [{"match": "*", "cmd": f"touch {marker}"}],
        })
        dispatch("PostToolUse", {"tool": "run_bash", "args": {}, "output": "done"})
        assert marker.is_file()


class TestMalformedConfig:
    def test_empty_json_ignored(self, isolated_hooks_config):
        isolated_hooks_config.write_text("{}")
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is True

    def test_broken_json_does_not_crash(self, isolated_hooks_config):
        isolated_hooks_config.write_text("{not json")
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is True  # fail-open on broken config

    def test_hook_without_cmd_skipped(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreToolUse": [{"match": "*"}],  # no cmd
        })
        r = dispatch("PreToolUse", {"tool": "run_bash", "args": {}})
        assert r.allowed is True


# ─── Full 9-event lifecycle coverage ────────────────────────────────────────

class TestUserPromptSubmit:
    @pytest.mark.skipif(sys.platform == "win32", reason="hook command uses POSIX shell syntax (>&2, ;) that cmd.exe doesn't understand")
    def test_block_on_nonzero(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "UserPromptSubmit": [{"match": "*", "cmd": ">&2 echo no; exit 1"}],
        })
        r = dispatch("UserPromptSubmit", {"prompt": "delete the db"})
        assert r.allowed is False
        assert "no" in r.stderr

    def test_match_filters_on_prompt_text(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "UserPromptSubmit": [{"match": "*delete*", "cmd": "exit 7"}],
        })
        # Non-matching prompt passes
        assert dispatch("UserPromptSubmit", {"prompt": "list files"}).allowed is True
        # Matching prompt blocks
        assert dispatch("UserPromptSubmit", {"prompt": "please delete this"}).allowed is False

    def test_apply_prompt_rewrite_passthrough(self, isolated_hooks_config):
        from omnicli.hooks import apply_prompt_rewrite
        _write_config(isolated_hooks_config, {
            "UserPromptSubmit": [{"match": "*", "cmd": "exit 0"}],  # empty stdout
        })
        ok, out = apply_prompt_rewrite({"prompt": "original"})
        assert ok is True
        assert out == "original"

    def test_apply_prompt_rewrite_stdout_rewrites(self, isolated_hooks_config):
        from omnicli.hooks import apply_prompt_rewrite
        _write_config(isolated_hooks_config, {
            "UserPromptSubmit": [{"match": "*", "cmd": "echo REWRITTEN"}],
        })
        ok, out = apply_prompt_rewrite({"prompt": "original"})
        assert ok is True
        assert out == "REWRITTEN"

    @pytest.mark.skipif(sys.platform == "win32", reason="hook command uses POSIX shell syntax (>&2, ;) that cmd.exe doesn't understand")
    def test_apply_prompt_rewrite_chain(self, isolated_hooks_config):
        """Second hook's stdout wins when both rewrite. First hook's output
        is passed as the `prompt` field in the next hook's stdin payload."""
        from omnicli.hooks import apply_prompt_rewrite
        # Second hook parses the JSON payload stdin and prints whatever
        # `prompt` it sees appended with "+B" — proves the rewrite chained in.
        py_script = (
            "python3 -c 'import sys,json; "
            "p=json.loads(sys.stdin.read())[\"prompt\"]; "
            "print(p+\"+B\")'"
        )
        _write_config(isolated_hooks_config, {
            "UserPromptSubmit": [
                {"match": "*", "cmd": "echo STEP1"},
                {"match": "*", "cmd": py_script},
            ],
        })
        ok, out = apply_prompt_rewrite({"prompt": "orig"})
        assert ok is True
        # First hook rewrote to "STEP1"; second hook saw prompt="STEP1" and emits "STEP1+B"
        assert out == "STEP1+B"

    @pytest.mark.skipif(sys.platform == "win32", reason="hook command uses POSIX shell syntax (>&2, ;) that cmd.exe doesn't understand")
    def test_apply_prompt_rewrite_block_returns_reason(self, isolated_hooks_config):
        from omnicli.hooks import apply_prompt_rewrite
        _write_config(isolated_hooks_config, {
            "UserPromptSubmit": [{"match": "*", "cmd": ">&2 echo NOPE; exit 1"}],
        })
        ok, out = apply_prompt_rewrite({"prompt": "x"})
        assert ok is False
        assert "NOPE" in out


class TestStop:
    def test_stop_is_informational(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "Stop": [{"match": "*", "cmd": "exit 99"}],
        })
        # Stop is non-blocking — allowed must stay True even if hook exited non-zero
        r = dispatch("Stop", {"final_text": "done"})
        assert r.allowed is True
        # But we still record the exit code for observability
        assert r.exit_code == 99


class TestSubagentStop:
    @pytest.mark.skipif(sys.platform == "win32", reason="hook command uses POSIX shell syntax (>&2, ;) that cmd.exe doesn't understand")
    def test_subagent_stop_is_informational(self, isolated_hooks_config, tmp_path):
        marker = tmp_path / "sub.touched"
        _write_config(isolated_hooks_config, {
            "SubagentStop": [{"match": "*", "cmd": f"touch {marker}; exit 0"}],
        })
        r = dispatch("SubagentStop", {"agent": "explore", "result": "done"})
        assert r.allowed is True
        assert marker.is_file()


class TestSessionStart:
    def test_session_start_fires(self, isolated_hooks_config, tmp_path):
        marker = tmp_path / "sess.started"
        _write_config(isolated_hooks_config, {
            "SessionStart": [{"match": "*", "cmd": f"touch {marker}"}],
        })
        r = dispatch("SessionStart", {"session_id": "abc"})
        assert r.allowed is True
        assert marker.is_file()


class TestSessionEnd:
    def test_session_end_fires(self, isolated_hooks_config, tmp_path):
        marker = tmp_path / "sess.ended"
        _write_config(isolated_hooks_config, {
            "SessionEnd": [{"match": "*", "cmd": f"touch {marker}"}],
        })
        r = dispatch("SessionEnd", {"session_id": "abc", "duration_s": 42})
        assert r.allowed is True
        assert marker.is_file()


class TestPreCompact:
    def test_pre_compact_is_informational(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "PreCompact": [{"match": "*", "cmd": ">&2 echo over_budget; exit 1"}],
        })
        # Non-blocking: must not prevent compaction even if hook exits non-zero
        r = dispatch("PreCompact", {"tokens": 180000, "budget": 200000})
        assert r.allowed is True
        assert "over_budget" in r.stderr


class TestNotification:
    def test_notification_level_filter(self, isolated_hooks_config, tmp_path):
        marker_err = tmp_path / "err.hit"
        marker_inf = tmp_path / "inf.hit"
        _write_config(isolated_hooks_config, {
            "Notification": [
                {"match": "error", "cmd": f"touch {marker_err}"},
                {"match": "info",  "cmd": f"touch {marker_inf}"},
            ],
        })
        dispatch("Notification", {"level": "error", "msg": "boom"})
        assert marker_err.is_file()
        assert not marker_inf.is_file()

    def test_notification_blocker_is_swallowed(self, isolated_hooks_config):
        _write_config(isolated_hooks_config, {
            "Notification": [{"match": "*", "cmd": "exit 3"}],
        })
        r = dispatch("Notification", {"level": "warn", "msg": "x"})
        # Notification is informational — allowed must remain True
        assert r.allowed is True
        assert r.exit_code == 3


class TestBlockingVsInformationalMatrix:
    """Exhaustive check: blocking events respect exit code, informational don't."""

    @pytest.mark.parametrize("event,blocking", [
        ("PreToolUse",       True),
        ("UserPromptSubmit", True),
        ("PostToolUse",      False),
        ("Stop",             False),
        ("SubagentStop",     False),
        ("SessionStart",     False),
        ("SessionEnd",       False),
        ("PreCompact",       False),
        ("Notification",     False),
    ])
    def test_event_blocking_contract(self, isolated_hooks_config, event, blocking):
        _write_config(isolated_hooks_config, {
            event: [{"match": "*", "cmd": "exit 9"}],
        })
        r = dispatch(event, {"tool": "x", "prompt": "y"})
        if blocking:
            assert r.allowed is False, f"{event} should be blocking"
        else:
            assert r.allowed is True,  f"{event} should be informational"
        # Both cases: exit code captured
        assert r.exit_code == 9
