"""Phantom thinking spinner — rich animated TUI for long-running LLM calls.

This is the v3 omnicli `PhantomSpinner` ported into the v4 phantom package
so the chat REPL has the same Claude-Code-style animation it had in v4.0.10
(Braille frames, rotating thinking verbs, elapsed time, token estimate).

Usage:

    from phantom.agent.spinner import PhantomSpinner
    spinner = PhantomSpinner()
    spinner.start()
    try:
        result = do_slow_llm_call()
    finally:
        spinner.stop(tokens=getattr(result, "total_tokens", 0))

Or as a context manager:

    with PhantomSpinner() as sp:
        result = do_slow_llm_call()
        sp.set_tokens(result.total_tokens)

The spinner runs in a daemon thread and writes to ``sys.stdout`` directly
(the chat REPL does the same so they coexist). On non-TTY stdout (piped
input, CI) ``start()`` is a no-op so log output stays clean.
"""

from __future__ import annotations

import itertools
import os
import random
import sys
import threading
import time
from typing import Callable, Optional

__all__ = ["PhantomSpinner", "with_spinner"]


# Braille spinner frames. Same cadence Claude Code uses.
_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

# Rotating verbs — JARVIS / Iron Man style + technical, picked at random
# every ~3 seconds to make long calls feel alive.
_VERBS = (
    "Thinking",
    "Analyzing",
    "Synthesizing",
    "Reasoning",
    "Inferring context",
    "Compiling neural sequences",
    "Routing to specialist",
    "Cross-referencing knowledge base",
    "Calibrating response matrix",
    "Mapping decision tree",
    "Deducing patterns",
    "Phantomizing",
    "Bending spacetime",
    "Summoning expertise",
    "Initialising deep reasoning",
    "Firing synapses",
    "Formulating response",
    "Triangulating data streams",
    "Consulting the oracle",
)


def _cyan(s: str) -> str:
    return f"\033[36m{s}\033[0m"


def _dim(s: str) -> str:
    return f"\033[2m{s}\033[0m"


def _fmt_elapsed(sec: float) -> str:
    s = int(sec)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


class PhantomSpinner:
    """Animated thinking spinner. Safe on non-TTY (becomes no-op)."""

    def __init__(
        self,
        *,
        stream=None,
        enabled: Optional[bool] = None,
        token_rate: int = 12,
    ):
        self._stream = stream or sys.stdout
        if enabled is None:
            # Auto: only animate when stdout is a TTY and we're not in tests.
            enabled = (
                hasattr(self._stream, "isatty")
                and self._stream.isatty()
                and os.environ.get("PHANTOM_NO_SPINNER") != "1"
            )
        self._enabled = enabled
        self._token_rate = token_rate
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._start_t = 0.0
        self._tokens = 0
        self._phase = "thinking"

    # ─── public ───────────────────────────────────────────────────────────

    def start(self, phase: str = "thinking") -> None:
        if not self._enabled or self._running:
            return
        self._running = True
        self._start_t = time.time()
        self._phase = phase
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_phase(self, phase: str) -> None:
        """Switch the verb (e.g. 'executing tool', 'streaming') mid-spin."""
        self._phase = phase

    def set_tokens(self, tokens: int) -> None:
        self._tokens = tokens

    def stop(self, tokens: int = 0, mark: str = "✓") -> None:
        if not self._enabled:
            return
        # v1.1.34: idempotent. Streaming kicks the spinner stop on first
        # chunk via _on_text_chunk; the main run_repl loop also calls
        # stop() after respond_to() returns. Without this guard, the
        # second stop's `\r` + spaces + `\r` erase sequence wipes the
        # streamed reply line because the cursor was sitting at the
        # start of "ghost › I'm Ghost!" — the user saw blank output.
        if not self._running:
            return
        self._running = False
        if tokens > 0:
            self._tokens = tokens
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        elapsed = time.time() - self._start_t
        # Erase the spinner line.
        self._stream.write("\r" + " " * 100 + "\r")
        # Print the summary.
        tok_str = f" · ↑ {self._tokens} tokens" if self._tokens > 0 else ""
        self._stream.write(_dim(f"{mark} done in {_fmt_elapsed(elapsed)}{tok_str}") + "\n")
        self._stream.flush()

    # Context manager.
    def __enter__(self) -> "PhantomSpinner":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # On exception, mark with ✗ instead of ✓.
        mark = "✓" if exc is None else "✗"
        self.stop(mark=mark)

    # ─── internal ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        frames = itertools.cycle(_FRAMES)
        verb = random.choice(_VERBS)
        next_verb_t = time.time() + random.uniform(2.5, 4.5)

        while self._running:
            now = time.time()
            elapsed = now - self._start_t
            frame = next(frames)
            if now >= next_verb_t:
                verb = random.choice(_VERBS)
                next_verb_t = now + random.uniform(2.5, 4.5)

            tok_est = self._tokens or int(elapsed * self._token_rate)
            elapsed_str = _fmt_elapsed(elapsed)

            phase_suffix = ""
            if self._phase == "executing":
                phase_suffix = " · executing"
            elif self._phase == "routing":
                phase_suffix = " · routing"

            meta = f"({elapsed_str} · ↑ {tok_est} tokens{phase_suffix})"
            line = (
                f"\r{_cyan(frame)} {verb}… {_dim(meta)}"
                + " " * 12
            )
            try:
                self._stream.write(line)
                self._stream.flush()
            except Exception:
                # If the stream got closed (REPL exit), bail silently.
                self._running = False
                return
            time.sleep(0.08)


def with_spinner(
    fn: Callable, *args, phase: str = "thinking", **kwargs,
):
    """Run ``fn(*args, **kwargs)`` with a spinner active. Returns whatever
    ``fn`` returns. The spinner stops with ✓ on success, ✗ on exception."""
    sp = PhantomSpinner()
    sp.start(phase=phase)
    try:
        result = fn(*args, **kwargs)
    except Exception:
        sp.stop(mark="✗")
        raise
    sp.stop()
    return result
