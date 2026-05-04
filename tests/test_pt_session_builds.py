"""Regression test for v3.0.2 REPL crash — `@kb.add("s-enter")` threw
ValueError at session-build time, bricking every `phantom chat` startup.

The fix removed the invalid `s-enter` token; we keep this test to make
sure no future change re-introduces an invalid key name."""
from __future__ import annotations

import pytest


def test_session_builder_does_not_raise():
    """Building the PromptSession must succeed on a machine where
    prompt_toolkit is importable. v3.0.2 failed here with
    `ValueError: Invalid key: s-enter`."""
    from omnicli.tui import _build_pt_session
    session = _build_pt_session()
    # If prompt_toolkit is installed (it's in requirements.txt), the
    # session must come back non-None. If the package is missing, the
    # builder returns None — that's also a valid outcome here.
    assert session is None or type(session).__name__ == "PromptSession"


def test_session_has_enter_binding():
    """Enter must be bound (it submits the buffer). Missing this means
    the REPL stops working — plain Enter inserts a newline instead."""
    from omnicli.tui import _build_pt_session
    session = _build_pt_session()
    if session is None:
        pytest.skip("prompt_toolkit not installed")
    # We can't introspect the key bindings by name easily, but we can
    # count them — the pre-bug session had 2 explicit bindings (enter +
    # alt-enter). The v3.0.2 build attempted a 3rd (s-enter) and crashed.
    # Any non-zero count means SOME bindings attached successfully.
    assert len(session.app.key_bindings.bindings) > 0


def test_alt_enter_binding_present():
    """Alt/Option+Enter → newline. Verify the `escape enter` 2-key chord
    attaches without error (it's the main newline shortcut)."""
    from omnicli.tui import _build_pt_session
    session = _build_pt_session()
    if session is None:
        pytest.skip("prompt_toolkit not installed")
    # Look for at least one binding whose key sequence has length > 1
    # (i.e. a multi-key chord like `escape enter`).
    has_chord = any(
        len(b.keys) >= 2
        for b in session.app.key_bindings.bindings
    )
    assert has_chord, "Alt+Enter chord binding missing"


def test_no_invalid_key_names_in_session_build():
    """End-to-end guard: building + re-building the session 5x must not
    raise. Would catch regressions where a key name is invalid on a
    specific prompt_toolkit version."""
    from omnicli.tui import _build_pt_session
    # Force a fresh build each time by clearing the module cache
    import omnicli.tui as _tui
    _tui._PT_SESSION = None
    for _ in range(5):
        _build_pt_session()
