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

def _isolate_terminal_state(monkeypatch):
    """Reset module-level state and disable pre-flight checks so the
    test can drive the Windows strategy path deterministically."""
    monkeypatch.setattr("phantom.cli._terminal._INITIALIZED", False)
    monkeypatch.setattr("phantom.cli._terminal._ANSI_OK", False)
    monkeypatch.setattr("phantom.cli._terminal._no_color_requested", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._stdout_is_redirected", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._is_dumb_terminal", lambda: False)


def test_enable_ansi_returns_true_on_posix(monkeypatch):
    """POSIX terminals with a TTY handle ANSI natively — function
    should report success without doing any Win32 calls."""
    _isolate_terminal_state(monkeypatch)
    monkeypatch.setattr("os.name", "posix")
    assert enable_ansi() is True


def test_enable_ansi_is_idempotent(monkeypatch):
    """Calling twice must be safe — no double init, no errors."""
    _isolate_terminal_state(monkeypatch)
    enable_ansi()
    # Second call: already initialised, fast-path.
    assert enable_ansi() is True


def test_enable_ansi_calls_setconsolemode_on_windows(monkeypatch):
    """On Windows, the function should call SetConsoleMode with the
    VT flag. v1.1.30 adds read-back verification — we mock the
    verifier to True to exercise the SetConsoleMode-succeeds path."""
    _isolate_terminal_state(monkeypatch)
    monkeypatch.setattr("os.name", "nt")
    # First strategy (os.system trick) won't enable VT in this test,
    # but the verifier sees True after _try_setconsolemode runs.
    verify_calls = {"n": 0}
    def fake_verify():
        verify_calls["n"] += 1
        return verify_calls["n"] >= 2  # False on first verify, True on second
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", fake_verify)
    setcm_called = []
    monkeypatch.setattr("phantom.cli._terminal._try_setconsolemode",
                        lambda: setcm_called.append(True))
    assert enable_ansi() is True
    assert setcm_called == [True]


def test_enable_ansi_falls_back_to_colorama_on_kernel32_failure(monkeypatch):
    """When os.system + SetConsoleMode both fail to enable VT (verifier
    keeps reporting False), colorama is the next attempt."""
    _isolate_terminal_state(monkeypatch)
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", lambda: False)

    called = []
    def fake_colorama():
        called.append(True)
        return True
    monkeypatch.setattr("phantom.cli._terminal._try_colorama", fake_colorama)
    assert enable_ansi() is True
    assert called == [True]


def test_enable_ansi_falls_back_to_strip_when_everything_fails(monkeypatch):
    """When verifier never returns True and colorama is unavailable,
    install the strip wrapper so output is monochrome but readable."""
    _isolate_terminal_state(monkeypatch)
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", lambda: False)
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
