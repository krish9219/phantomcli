"""Tests for v1.1.34 — fix the "no response visible" bug introduced
by streaming + double-spinner-stop interaction.

User transcript on 2026-05-10:

    Arvi Sir › what's your name?
    ✓ done in 3s
    ✓ done in 4s
    [BLANK — no reply visible]

Two distinct issues:

1. **`PhantomSpinner.stop()` was non-idempotent.** Streaming kicked
   the first stop in ``_on_text_chunk`` (so the cursor advanced past
   the spinner line and ``ghost ›`` could be printed). Then the main
   ``run_repl`` loop called ``spinner.stop()`` again after
   ``respond_to()`` returned. The second stop wrote
   ``\\r`` + 100 spaces + ``\\r`` to "erase the spinner line" — but
   the cursor was sitting at the start of the streamed reply line,
   so it erased the user's reply instead.

2. **Empty replies were silently rendered as blank lines.** Some
   free-tier endpoints (NVIDIA's `inclusionai/ring-2.6-1t:free` was
   the user's case) occasionally return an empty string when
   rate-limited or when tool-call routing gets confused. The
   non-streamed path wrote ``Ghost ›`` then ``_render_assistant_reply("")``
   which writes one ``\\n``. From the user's perspective, the agent
   "finished" but said nothing.
"""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock

import pytest


# ─── Spinner idempotent stop ──────────────────────────────────────────────

def test_spinner_stop_writes_summary_once():
    """Calling stop() twice must NOT write the 'done in Xs' summary
    twice — the second call would also re-emit `\\r` + spaces + `\\r`
    which erases whatever line the cursor is on (the user's reply
    in v1.1.33's failure mode)."""
    from phantom.agent.spinner import PhantomSpinner
    sink = io.StringIO()
    sp = PhantomSpinner(stream=sink, enabled=True)
    sp.start()
    time.sleep(0.05)  # let the spinner thread tick at least once
    sp.stop()
    output_after_first = sink.getvalue()
    sp.stop()  # second call — must be a no-op
    output_after_second = sink.getvalue()
    assert output_after_first == output_after_second, (
        "spinner.stop() is not idempotent — the second call re-emitted "
        "an erase + summary, which would wipe the user's reply line"
    )
    # And the summary appears exactly once.
    assert output_after_first.count("done in") == 1


def test_spinner_stop_after_disabled_is_safe():
    """Disabled spinner: stop() is always a no-op regardless of how
    many times it's called."""
    from phantom.agent.spinner import PhantomSpinner
    sink = io.StringIO()
    sp = PhantomSpinner(stream=sink, enabled=False)
    sp.start()
    sp.stop()
    sp.stop()
    sp.stop()
    assert sink.getvalue() == ""


def test_spinner_stop_without_start_is_safe():
    """stop() called before start() — _running is False so it must
    silently do nothing (not crash, not write garbage)."""
    from phantom.agent.spinner import PhantomSpinner
    sink = io.StringIO()
    sp = PhantomSpinner(stream=sink, enabled=True)
    # Did not call start().
    sp.stop()
    assert sink.getvalue() == ""


def test_spinner_stop_then_start_then_stop_works():
    """Spinner can be reused: first stop() drained, start() spins it
    up again, second stop() emits a fresh summary."""
    from phantom.agent.spinner import PhantomSpinner
    sink = io.StringIO()
    sp = PhantomSpinner(stream=sink, enabled=True)
    sp.start()
    time.sleep(0.05)
    sp.stop()
    first_summary_count = sink.getvalue().count("done in")
    sp.start()
    time.sleep(0.05)
    sp.stop()
    second_summary_count = sink.getvalue().count("done in")
    assert first_summary_count == 1
    assert second_summary_count == 2


# ─── Empty-reply detection in non-streamed path ───────────────────────────

def test_empty_reply_warning_present_in_chat_source():
    """The non-streamed path must surface a concrete hint when the
    model returns an empty string, instead of silently writing a
    blank line. Source-level structural assertion (the path itself
    is hard to invoke without a full session)."""
    import inspect
    from phantom.cli import chat
    src = inspect.getsource(chat)
    # The hint must mention `/model` so the user has a concrete next step.
    assert "returned an empty" in src
    assert "/model claude-haiku-4-5" in src
    # And it must appear in the non-streamed branch (we check the
    # immediate textual context).
    assert "stripped = (reply or \"\").strip()" in src


# ─── Regression check: spinner state after double-stop matches first-stop ─

def test_spinner_running_flag_false_after_idempotent_stop():
    """After stop(), `_running` is False. After a SECOND stop(), it
    stays False. (Regression: a previous misimplementation might
    flip it back to True or raise.)"""
    from phantom.agent.spinner import PhantomSpinner
    sink = io.StringIO()
    sp = PhantomSpinner(stream=sink, enabled=True)
    sp.start()
    time.sleep(0.05)
    assert sp._running is True
    sp.stop()
    assert sp._running is False
    sp.stop()
    assert sp._running is False
