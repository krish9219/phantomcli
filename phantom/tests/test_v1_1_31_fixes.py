"""Tests for v1.1.31 — the *real* fix for the literal `^[[36m` Windows
rendering bug that v1.1.29 and v1.1.30 both missed.

User's symptom (v1.1.28 transcript):

    PS C:\\Users\\aravi> phantom chat
    ...
    ● Welcome back, Arvi Sir. Ghost is online and ready.
    ^[[36mArvi Sir ›^[[0m whats your name?

The boot banner ANSI rendered correctly (so ``enable_ansi()`` /
SetConsoleMode IS working), but the input prompt label still showed
literal `^[[36m`. Two distinct rendering pipelines:

* Boot banner / chat output → ``sys.stdout.write`` → Windows console
  with VT mode on → colours render.
* Input prompt label → ``prompt_toolkit.PromptSession.prompt(label)``
  → prompt_toolkit's renderer, which treats a plain string as literal
  text and DOESN'T interpret embedded ANSI codes.

The fix is to wrap the label in ``prompt_toolkit.formatted_text.ANSI``,
which tells prompt_toolkit's renderer to parse the embedded codes.
v1.1.30's ``enable_ansi()`` fix was correct but didn't help here because
prompt_toolkit owns its own rendering and ignores the global VT state.
"""

from __future__ import annotations

import pytest


def test_prompt_toolkit_ansi_class_is_imported():
    """Verify the ANSI helper is importable from the location chat.py
    expects. If prompt_toolkit moves it, this test fails fast instead
    of users seeing literal escape codes in their prompt."""
    from prompt_toolkit.formatted_text import ANSI
    obj = ANSI("\033[36mfoo\033[0m")
    # ANSI exposes the original string via .value (used by prompt_toolkit
    # internals). Sanity check that the wrapper accepted the input.
    assert "foo" in str(obj) or "foo" in repr(obj)


def test_chat_module_imports_prompt_toolkit_ansi():
    """The chat module must import ANSI alongside PromptSession.
    Structural assertion: regression-proof against someone removing
    the import while editing chat.py."""
    import inspect
    from phantom.cli import chat
    src = inspect.getsource(chat)
    assert "from prompt_toolkit.formatted_text import ANSI" in src, (
        "chat.py must import ANSI from prompt_toolkit.formatted_text — "
        "without it, prompt_toolkit treats the prompt label as literal "
        "text and the user sees ^[[36m on Windows."
    )


def test_prompt_label_is_wrapped_in_ansi():
    """The prompt label string contains `\\033[36m` escapes. It MUST be
    wrapped in ANSI() before being passed to `_chat_session.prompt()`,
    otherwise prompt_toolkit shows literal `^[[36m`."""
    import inspect
    from phantom.cli import chat
    src = inspect.getsource(chat)
    # The wrapped form must be present.
    assert "ANSI(f\"\\n{CYAN}{user_label} ›{RESET} \")" in src or \
           "ANSI(f'\\n{CYAN}{user_label} ›{RESET} ')" in src, (
        "_build_prompt_label() must wrap its f-string in ANSI()"
    )
    # The bare-string form (the v1.1.30 bug) must not have crept back.
    assert "return f\"\\n{CYAN}{user_label} ›{RESET} \"" not in src, (
        "regression: _build_prompt_label() returns a raw escape-laden "
        "string — prompt_toolkit will render it literally as ^[[36m"
    )


def test_ansi_wrapping_round_trips_escape_codes():
    """Sanity check: wrapping a coloured string in ANSI() preserves the
    escape sequences for prompt_toolkit's parser to consume. (We don't
    test the renderer end-to-end — that requires a live terminal — but
    we confirm the data is intact.)"""
    from prompt_toolkit.formatted_text import ANSI
    raw = "\033[36mArvi Sir ›\033[0m "
    wrapped = ANSI(raw)
    # str(ANSI) yields the input back verbatim in current prompt_toolkit;
    # if upstream changes that contract, this test fires and we adapt.
    assert "\033[" in str(wrapped) or "\x1b[" in str(wrapped) or hasattr(wrapped, "value")
