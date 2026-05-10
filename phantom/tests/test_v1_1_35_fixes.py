"""Tests for v1.1.35 — disable streaming on Windows where it's flaky.

User transcript on 2026-05-10 (running v1.1.34 on PowerShell):

    Arvi Sir › what's your name?
    ✓ done in 7s
     — a coding agent that runs on a configurable model.   ← prefix + "I'm Ghost" missing

    Arvi Sir › what model are you?
    ✓ done in 1s
    'm Ghost — a coding agent that runs on a configurable model.   ← "I" missing

The streaming write path (chunked ``sys.stdout.write`` via the
``_on_text_chunk`` callback) interacts poorly with PowerShell 5.1's
console rendering even with VT mode on. The cursor positioning
glitches eat the ``ghost ›`` prefix and leading characters of the
reply.

The non-streamed render path (``_render_assistant_reply`` → rich
markdown → colorama-wrapped stdout on Windows) is well-tested and
renders cleanly. v1.1.35 disables streaming by default on Windows
and falls back to that path. Streaming stays enabled on POSIX.

Set ``PHANTOM_STREAMING=1`` to force streaming on Windows anyway.
"""

from __future__ import annotations

import inspect

import pytest


# ─── Streaming disabled by default on Windows ─────────────────────────────

def test_streaming_disabled_on_windows_by_default():
    """The chat module must gate ``session.on_text_chunk`` on
    ``os.name`` so Windows users hit the non-streamed render path.
    Source-level structural assertion since the binding lives inside
    ``chat()`` which is hard to invoke without a full session."""
    from phantom.cli import chat
    src = inspect.getsource(chat)
    # The gate condition must be present and target os.name == 'nt'.
    assert "os.name != \"nt\"" in src
    # The opt-in env var must be honoured.
    assert "PHANTOM_STREAMING" in src
    # Non-streamed branch must still set _phantom_stream_state so
    # run_repl can reset it (it just won't have started=True chunks).
    assert "session.on_text_chunk = None" in src


def test_streaming_explanation_documented():
    """The reason for the Windows disable must remain in the source as
    a comment so future maintainers don't re-enable it without
    understanding the v1.1.34 user-reported bug."""
    from phantom.cli import chat
    src = inspect.getsource(chat)
    # Must mention the symptom (cursor positioning eating prefix /
    # leading chars) so the disable isn't mysterious.
    assert "cursor positioning" in src.lower() or "chunked writes" in src.lower()
    assert "v1.1.34" in src or "v1.1.35" in src


# ─── PHANTOM_STREAMING=1 escape hatch ─────────────────────────────────────

def test_streaming_can_be_force_enabled_via_env_var(monkeypatch):
    """Power users on Windows who fixed their console (e.g., upgraded
    to Windows Terminal + PowerShell 7) can force streaming via
    PHANTOM_STREAMING=1. Source-level check that the opt-in is honoured."""
    from phantom.cli import chat
    src = inspect.getsource(chat)
    # The env var check must be an OR with os.name != 'nt'.
    assert 'os.environ.get("PHANTOM_STREAMING")' in src
    assert "== \"1\"" in src


# ─── on_text_chunk binding logic ──────────────────────────────────────────

def test_streaming_enabled_flag_drives_session_binding():
    """The `_streaming_enabled` flag controls whether
    `session.on_text_chunk` is bound to the live callback or set to
    None. Both branches must update `_phantom_stream_state` so run_repl
    can still reset it per turn (avoiding stale-state bugs)."""
    from phantom.cli import chat
    src = inspect.getsource(chat)
    assert "if _streaming_enabled:" in src
    assert "session.on_text_chunk = _on_text_chunk" in src
    assert "session.on_text_chunk = None" in src
    # _phantom_stream_state set in BOTH branches (regression net):
    # the streamed branch and the non-streamed branch both set it.
    assert src.count("session._phantom_stream_state = _stream_state") == 2
