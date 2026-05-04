"""
PhantomCLI Visuals
─────────────────
Includes the Claude Code-style thinking spinner and terminal chart renderer.

Spinner format mirrors Claude Code:
  ✢ Phantomizing… (1m 33s · ↑ 615 tokens · thought for 3s)
"""

import threading
import time
import sys
import random
import itertools

import plotext as plt
from rich.console import Console

console = Console()

# ─── SPINNER CONFIG ────────────────────────────────────────────────────────────

# Braille-style spinner frames (same cadence as Claude Code)
_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

# Rotating symbols shown before the verb (Claude Code uses ✢ and similar)
_SYMBOLS = itertools.cycle(['✢', '◆', '✦', '❋', '✿', '✸', '✺', '❊', '✱', '❃'])

# Thinking verbs — mix of real and whimsical, rotating every ~3s
_VERBS = [
    # JARVIS / Iron Man style
    "Running diagnostics",
    "Cross-referencing databases",
    "Accessing global network",
    "Calculating probability matrix",
    "Compiling neural sequences",
    "Interfacing with AI core",
    "Triangulating data streams",
    "Deploying cognitive subroutines",
    "Analysing threat vectors",
    "Parsing semantic structures",
    "Synchronising knowledge base",
    "Establishing secure uplink",
    "Extrapolating from data set",
    "Overclocking inference engine",
    "Mapping decision tree",
    "Calibrating response matrix",
    "Consulting the oracle",
    "Routing to specialist",
    "Initialising deep reasoning",
    "Phantomizing the answer",
    "Bending spacetime",
    "Summoning expertise",
    # Technical
    "Analyzing",
    "Synthesizing",
    "Reasoning",
    "Evaluating",
    "Processing",
    "Orchestrating",
    "Firing synapses",
    "Inferring context",
    "Deducing patterns",
    "Formulating response",
]


# ─── ANSI HELPERS ─────────────────────────────────────────────────────────────

def _cyan(s: str)   -> str: return f'\033[36m{s}\033[0m'
def _dim(s: str)    -> str: return f'\033[2m{s}\033[0m'
def _bold(s: str)   -> str: return f'\033[1m{s}\033[0m'
def _green(s: str)  -> str: return f'\033[32m{s}\033[0m'
def _yellow(s: str) -> str: return f'\033[33m{s}\033[0m'


def _fmt_elapsed(sec: float) -> str:
    """Format elapsed seconds as '45s' or '1m 33s'."""
    s = int(sec)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


# ─── PHANTOM SPINNER ──────────────────────────────────────────────────────────

class PhantomSpinner:
    """
    Claude Code-style spinner for long-running AI calls.

    Usage:
        spinner = PhantomSpinner()
        spinner.start()
        result = do_slow_ai_call()
        spinner.stop(tokens=result.total_tokens)
    """

    def __init__(self):
        self._running   = False
        self._thread    = None
        self._start_t   = 0.0
        self._tokens    = 0
        self._phase     = "thinking"   # "thinking" | "executing" | "routing"
        self._done_line = ""           # summary line printed after stop

    # ── public ────────────────────────────────────────────────────────────────

    def start(self, phase: str = "thinking") -> None:
        self._running = True
        self._start_t = time.time()
        self._phase   = phase
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_phase(self, phase: str) -> None:
        """Switch the phase label mid-spin: 'routing', 'executing', 'thinking'."""
        self._phase = phase

    def stop(self, tokens: int = 0) -> None:
        self._running = False
        self._tokens  = tokens
        if self._thread:
            self._thread.join(timeout=0.5)
        elapsed = time.time() - self._start_t
        # Erase spinner line
        sys.stdout.write('\r' + ' ' * 100 + '\r')
        sys.stdout.flush()
        # Print summary line
        tok_str = f" · ↑ {tokens} tokens" if tokens > 0 else ""
        summary = _dim(f"✓ Done in {_fmt_elapsed(elapsed)}{tok_str}")
        sys.stdout.write(summary + '\n')
        sys.stdout.flush()

    # ── internal ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        frames      = itertools.cycle(_FRAMES)
        verb        = random.choice(_VERBS)
        next_verb_t = time.time() + random.uniform(2.5, 4.5)

        # Estimate tokens at ~12 tokens/sec (rough for typical models)
        TOKEN_RATE  = 12

        while self._running:
            now     = time.time()
            elapsed = now - self._start_t
            frame   = next(frames)

            # Rotate verb
            if now >= next_verb_t:
                verb        = random.choice(_VERBS)
                next_verb_t = now + random.uniform(2.5, 4.5)

            tok_est = int(elapsed * TOKEN_RATE)
            elapsed_str = _fmt_elapsed(elapsed)

            # Phase label shown in dim parentheses
            phase_labels = {
                "routing":   "routing…",
                "executing": "executing…",
                "thinking":  None,
            }
            phase_suffix = phase_labels.get(self._phase)

            # Build the line
            #  ⠋ Phantomizing… (1m 33s · ↑ 615 tokens)
            meta_parts = [elapsed_str, f"↑ {tok_est} tokens"]
            if phase_suffix:
                meta_parts.append(phase_suffix)
            meta = " · ".join(meta_parts)

            line = (
                f"\r{_cyan(frame)} "
                f"{verb}… "
                f"{_dim('(' + meta + ')')}"
                "          "   # trailing spaces to overwrite previous longer lines
            )

            sys.stdout.write(line)
            sys.stdout.flush()
            time.sleep(0.08)

        # Final erase happens in stop()


# ─── CONVENIENCE WRAPPER ──────────────────────────────────────────────────────

def with_spinner(fn, *args, phase="thinking", **kwargs):
    """
    Run fn(*args, **kwargs) with a PhantomSpinner active.
    Returns (result, spinner) — call spinner.stop(tokens=N) yourself,
    or use the returned spinner's elapsed time.

    Example:
        result, sp = with_spinner(generate_response, prompt, history, trust)
        sp.stop(tokens=result.get('tokens', 0))
    """
    sp = PhantomSpinner()
    sp.start(phase=phase)
    try:
        result = fn(*args, **kwargs)
    except Exception:
        sp.stop()
        raise
    return result, sp


# ─── TERMINAL CHARTS ──────────────────────────────────────────────────────────

def render_terminal_chart(title: str, x_data: list, y_data: list, chart_type: str = "line"):
    """
    Renders a high-resolution chart directly in the terminal using Braille.
    """
    plt.clf()
    plt.theme("dark")
    plt.title(title)

    if chart_type == "bar":
        plt.bar(x_data, y_data)
    elif chart_type == "scatter":
        plt.scatter(x_data, y_data, marker="braille")
    else:
        plt.plot(x_data, y_data, marker="braille")

    console.print("\n")
    plt.show()
    console.print("\n")
    return f"Successfully rendered {chart_type} chart: '{title}' to the user's terminal."
