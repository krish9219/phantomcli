"""Tests for the PhantomSpinner — small unit checks; the animation itself
runs in a daemon thread that we don't drive deterministically here."""

from __future__ import annotations

import io
import time

from phantom.agent.spinner import PhantomSpinner, with_spinner


def test_disabled_when_stream_is_not_tty():
    """Default behavior on a piped stream: no animation, no output."""
    buf = io.StringIO()
    sp = PhantomSpinner(stream=buf)
    sp.start()
    sp.stop()
    assert buf.getvalue() == ""


def test_explicit_enable_writes_summary_line():
    """When forced enabled, stop() prints a `done in <elapsed>` summary."""
    buf = io.StringIO()
    sp = PhantomSpinner(stream=buf, enabled=True)
    sp.start()
    time.sleep(0.05)
    sp.stop(tokens=42)
    output = buf.getvalue()
    assert "done in" in output
    assert "42 tokens" in output


def test_with_spinner_returns_function_result():
    """The convenience wrapper must propagate the wrapped fn's return value."""
    def inner(x): return x * 2
    # On a non-TTY stream the spinner is auto-disabled; we just want to
    # confirm with_spinner doesn't swallow the return value.
    assert with_spinner(inner, 7) == 14


def test_with_spinner_propagates_exceptions():
    def boom(): raise ValueError("nope")
    import pytest
    with pytest.raises(ValueError, match="nope"):
        with_spinner(boom)


def test_context_manager_marks_failure_on_exception():
    buf = io.StringIO()
    try:
        with PhantomSpinner(stream=buf, enabled=True) as sp:
            time.sleep(0.05)
            raise RuntimeError("x")
    except RuntimeError:
        pass
    assert "✗" in buf.getvalue()
