"""Tests for v1.1.28 — Windows VT mode init + knowledge-vs-tool prompt clause.

Triggered by the v1.1.27 user transcript:
- ANSI escape codes rendered as literal `^[[36m` in PowerShell because
  ENABLE_VIRTUAL_TERMINAL_PROCESSING wasn't set.
- Prompt #1 ("explain async/await") burned 5min writing async_explainer.py
  instead of just streaming a markdown answer — the system prompt over-
  biased toward tool calls for ALL questions.
- Pasted-text content was echoed in full + a redundant summary line.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phantom.agent.session import DEFAULT_SYSTEM_PROMPT
from phantom.cli._terminal import enable_ansi


# ─── enable_ansi ────────────────────────────────────────────────────────────

def test_enable_ansi_returns_true_on_posix(monkeypatch):
    """POSIX terminals already handle ANSI — function should report
    success without doing any Win32 calls."""
    monkeypatch.setattr("os.name", "posix")
    # Reset the idempotency cache.
    monkeypatch.setattr("phantom.cli._terminal._INITIALIZED", False)
    assert enable_ansi() is True


def test_enable_ansi_is_idempotent(monkeypatch):
    """Calling twice must be safe — no double init, no errors."""
    monkeypatch.setattr("phantom.cli._terminal._INITIALIZED", False)
    enable_ansi()
    # Second call: already initialised, fast-path.
    assert enable_ansi() is True


def test_enable_ansi_calls_setconsolemode_on_windows(monkeypatch):
    """On Windows, the function should resolve kernel32 + call
    SetConsoleMode with the VT flag for both stdout and stderr handles."""
    monkeypatch.setattr("phantom.cli._terminal._INITIALIZED", False)
    monkeypatch.setattr("os.name", "nt")

    fake_kernel32 = MagicMock()
    fake_kernel32.GetStdHandle.side_effect = [100, 101]  # 2 distinct handles
    fake_kernel32.GetConsoleMode.return_value = True
    fake_kernel32.SetConsoleMode.return_value = True

    fake_ctypes_module = MagicMock()
    fake_ctypes_module.windll.kernel32 = fake_kernel32
    fake_ctypes_module.byref = lambda x: x  # passthrough
    fake_ctypes_module.c_void_p = MagicMock()
    fake_ctypes_module.c_void_p.return_value.value = -1

    with patch.dict("sys.modules", {"ctypes": fake_ctypes_module,
                                     "ctypes.wintypes": MagicMock()}):
        assert enable_ansi() is True
    # SetConsoleMode called for stdout + stderr.
    assert fake_kernel32.SetConsoleMode.call_count == 2
    # Each call ORs in 0x4 (ENABLE_VIRTUAL_TERMINAL_PROCESSING).
    for call in fake_kernel32.SetConsoleMode.call_args_list:
        new_mode = call.args[1]
        assert new_mode & 0x0004


def test_enable_ansi_falls_back_to_colorama_on_kernel32_failure(monkeypatch):
    """v1.1.29 stacks 4 strategies. If os.system AND SetConsoleMode both
    fail, colorama is the next attempt. Verify it gets called.
    """
    monkeypatch.setattr("phantom.cli._terminal._INITIALIZED", False)
    monkeypatch.setattr("phantom.cli._terminal._ANSI_OK", False)
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr("phantom.cli._terminal._try_os_system_trick", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._try_setconsolemode", lambda: False)

    called = []
    def fake_colorama():
        called.append(True)
        return True
    monkeypatch.setattr("phantom.cli._terminal._try_colorama", fake_colorama)

    assert enable_ansi() is True
    assert called == [True]


def test_enable_ansi_falls_back_to_strip_when_everything_fails(monkeypatch):
    """v1.1.29: when no native path works, install the strip wrapper
    so output is monochrome but readable. Returns False so the caller
    can know native colours won't render."""
    monkeypatch.setattr("phantom.cli._terminal._INITIALIZED", False)
    monkeypatch.setattr("phantom.cli._terminal._ANSI_OK", False)
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr("phantom.cli._terminal._try_os_system_trick", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._try_setconsolemode", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._try_colorama", lambda: False)

    installed = []
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper",
                         lambda: installed.append(True) or True)
    assert enable_ansi() is False
    assert installed == [True]


# ─── Knowledge-vs-tool clause in DEFAULT_SYSTEM_PROMPT ──────────────────────

def test_system_prompt_distinguishes_knowledge_questions_from_tool_tasks():
    """The v1.1.27 'act, don't narrate' rule was overzealous — it told
    the model to use tools for everything. v1.1.28 carves out
    knowledge questions explicitly."""
    low = DEFAULT_SYSTEM_PROMPT.lower()
    # Section header about tool boundaries.
    assert "when to use tools" in low or "do not use tools for pure-knowledge" in low
    # Concrete don't-use-tools examples that match the v1.1.27 failure.
    assert "explain" in low
    assert "compare" in low or "what is" in low
    # Markdown rendering reminder so the model knows the reply will be
    # formatted nicely.
    assert "markdown" in low


def test_system_prompt_still_has_act_dont_narrate_for_real_tasks():
    """Don't regress the v1.1.16 fix — for actual operations the model
    must still call tools instead of describing them."""
    low = DEFAULT_SYSTEM_PROMPT.lower()
    assert "act, don't narrate" in low
    assert "without calling write_file is a failure" in low
