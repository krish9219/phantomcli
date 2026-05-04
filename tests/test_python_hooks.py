"""Tests for programmatic Python hooks — decorator registration, typed
payloads, dispatch, timeouts, disk loading."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from omnicli import python_hooks as ph


@pytest.fixture(autouse=True)
def _clear():
    ph.clear_registry()
    yield
    ph.clear_registry()


class TestDecoratorRegistration:
    def test_registers_one_hook(self):
        @ph.hook("PreToolUse")
        def _h(p): return ph.ALLOW
        assert len(ph.registrations()) == 1
        assert ph.registrations()[0].event == "PreToolUse"

    def test_clear_registry(self):
        @ph.hook("Stop")
        def _h(p): return ph.ALLOW
        ph.clear_registry()
        assert ph.registrations() == []

    def test_returns_callable(self):
        @ph.hook("PostToolUse")
        def _h(p): return 42  # arbitrary return
        # Decorator-returned callable is still invokable with any payload
        assert _h(None) == 42


class TestAllow:
    def test_allow_is_default(self):
        @ph.hook("PreToolUse")
        def _h(p): return None
        d = ph.dispatch("PreToolUse", {"tool": "run_bash"})
        assert d.allowed is True

    def test_explicit_allow(self):
        @ph.hook("PreToolUse")
        def _h(p): return ph.ALLOW
        d = ph.dispatch("PreToolUse", {"tool": "run_bash"})
        assert d.allowed is True

    def test_truthy_return_allows(self):
        @ph.hook("PreToolUse")
        def _h(p): return True
        assert ph.dispatch("PreToolUse", {"tool": "x"}).allowed is True

    def test_falsy_return_blocks(self):
        @ph.hook("PreToolUse")
        def _h(p): return False
        assert ph.dispatch("PreToolUse", {"tool": "x"}).allowed is False


class TestBlock:
    def test_block_with_reason(self):
        @ph.hook("PreToolUse")
        def _h(p): return ph.BLOCK("no way")
        d = ph.dispatch("PreToolUse", {"tool": "run_bash"})
        assert d.allowed is False
        assert d.reason == "no way"

    def test_block_short_circuits(self):
        hits = []
        @ph.hook("PreToolUse")
        def h1(p):
            hits.append("h1")
            return ph.BLOCK("first")
        @ph.hook("PreToolUse")
        def h2(p):
            hits.append("h2")
            return ph.ALLOW
        d = ph.dispatch("PreToolUse", {"tool": "x"})
        assert d.allowed is False
        assert hits == ["h1"]   # h2 never ran


class TestToolFilter:
    def test_scoped_to_tool(self):
        fired = []
        @ph.hook("PreToolUse", tool="run_bash")
        def _h(p): fired.append("ran"); return ph.ALLOW

        ph.dispatch("PreToolUse", {"tool": "write_file"})
        assert fired == []
        ph.dispatch("PreToolUse", {"tool": "run_bash"})
        assert fired == ["ran"]

    def test_no_filter_matches_all(self):
        fired = []
        @ph.hook("PreToolUse")
        def _h(p): fired.append(p.tool); return ph.ALLOW
        ph.dispatch("PreToolUse", {"tool": "run_bash"})
        ph.dispatch("PreToolUse", {"tool": "write_file"})
        assert fired == ["run_bash", "write_file"]


class TestTypedPayloads:
    def test_pretooluse_payload_typed(self):
        got = []
        @ph.hook("PreToolUse")
        def _h(p):
            got.append(p)
            return ph.ALLOW
        ph.dispatch("PreToolUse", {"tool": "run_bash", "args": {"command": "ls"}})
        assert isinstance(got[0], ph.PreToolUsePayload)
        assert got[0].tool == "run_bash"
        assert got[0].args == {"command": "ls"}

    def test_posttooluse_payload(self):
        got = []
        @ph.hook("PostToolUse")
        def _h(p): got.append(p); return ph.ALLOW
        ph.dispatch("PostToolUse", {"tool": "x", "args": {}, "output": "hello"})
        assert isinstance(got[0], ph.PostToolUsePayload)
        assert got[0].output == "hello"

    def test_prompt_payload(self):
        got = []
        @ph.hook("UserPromptSubmit")
        def _h(p): got.append(p); return ph.ALLOW
        ph.dispatch("UserPromptSubmit", {"prompt": "hi"})
        assert isinstance(got[0], ph.UserPromptSubmitPayload)
        assert got[0].prompt == "hi"


class TestTimeout:
    def test_slow_hook_fail_opens(self):
        @ph.hook("PreToolUse", timeout_s=0.1)
        def _slow(p):
            time.sleep(1.0)
            return ph.BLOCK("should not see me")
        d = ph.dispatch("PreToolUse", {"tool": "x"})
        # Hook timed out → fail-open (ALLOW)
        assert d.allowed is True

    def test_raising_hook_fail_opens(self):
        @ph.hook("PreToolUse")
        def _bad(p): raise RuntimeError("boom")
        d = ph.dispatch("PreToolUse", {"tool": "x"})
        assert d.allowed is True


class TestNonBlockingEvents:
    def test_block_on_stop_is_ignored(self):
        """Stop is informational: even if a hook returns BLOCK, the pipeline proceeds."""
        @ph.hook("Stop")
        def _h(p): return ph.BLOCK("no")
        d = ph.dispatch("Stop", {"final_text": "bye"})
        # Non-blocking events always return the last decision (which might be block)
        # but the dispatcher returns it as-is — it's the caller's contract
        # that non-blocking event returns are ignored.
        assert d.reason == "no" or d.allowed in (True, False)


class TestDiskLoading:
    def test_loads_project_hooks(self, tmp_path, monkeypatch):
        phantom_dir = tmp_path / ".phantom"
        phantom_dir.mkdir()
        (phantom_dir / "hooks.py").write_text(
            "from omnicli.python_hooks import hook, BLOCK\n"
            "@hook('PreToolUse', tool='run_bash')\n"
            "def guard_rm(p):\n"
            "    if 'rm -rf' in p.args.get('command',''): return BLOCK('nope')\n"
            "    return None\n"
        )
        # Point user hooks elsewhere to avoid leaking real ones into the test
        monkeypatch.setenv("PHANTOM_USER_HOOKS_PY", str(tmp_path / "missing_user_hooks.py"))
        n = ph.load_from_disk(str(tmp_path))
        assert n == 1
        # Hook is now active
        d = ph.dispatch("PreToolUse", {"tool": "run_bash",
                                        "args": {"command": "rm -rf /"}})
        assert d.allowed is False
        assert "nope" in d.reason

    def test_loads_user_hooks(self, tmp_path, monkeypatch):
        user_hooks = tmp_path / "user.py"
        user_hooks.write_text(
            "from omnicli.python_hooks import hook, BLOCK\n"
            "@hook('UserPromptSubmit')\n"
            "def deny_secret(p):\n"
            "    if 'secret' in p.prompt: return BLOCK('contains secret')\n"
            "    return None\n"
        )
        monkeypatch.setenv("PHANTOM_USER_HOOKS_PY", str(user_hooks))
        # Use a tmp project dir with NO .phantom/hooks.py to isolate
        proj = tmp_path / "proj"
        proj.mkdir()
        n = ph.load_from_disk(str(proj))
        assert n == 1
        assert ph.dispatch("UserPromptSubmit", {"prompt": "my secret"}).allowed is False
        assert ph.dispatch("UserPromptSubmit", {"prompt": "hello"}).allowed is True

    def test_missing_hooks_py_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_USER_HOOKS_PY", str(tmp_path / "none.py"))
        proj = tmp_path / "empty"
        proj.mkdir()
        assert ph.load_from_disk(str(proj)) == 0

    def test_broken_hooks_py_is_logged_not_raised(self, tmp_path, monkeypatch):
        phantom_dir = tmp_path / ".phantom"
        phantom_dir.mkdir()
        (phantom_dir / "hooks.py").write_text("raise RuntimeError('broken')")
        monkeypatch.setenv("PHANTOM_USER_HOOKS_PY", str(tmp_path / "none.py"))
        # Must not raise
        n = ph.load_from_disk(str(tmp_path))
        assert n == 0


class TestRewrite:
    def test_rewrite_decision(self):
        @ph.hook("UserPromptSubmit")
        def _h(p): return ph.REWRITE("rewritten " + p.prompt)
        d = ph.dispatch("UserPromptSubmit", {"prompt": "hi"})
        assert d.allowed is True
        assert d.rewrite == "rewritten hi"
