"""Phantom render — terminal-side rendering of structured content.

Today
-----

* :mod:`phantom.render.mermaid` — mermaid diagram rendering with
  per-terminal capability detection (kitty graphics protocol, sixel,
  ASCII fallback).

Later (out of scope this session) — markdown table rendering, code
syntax highlighting, ANSI sparkline. Each renderer follows the same
shape: detect capabilities, attempt the best path, fall back gracefully.
"""

from __future__ import annotations

from phantom.render.mermaid import (
    MermaidRenderer,
    TerminalCapabilities,
    detect_terminal_capabilities,
    render_mermaid,
)

__all__ = [
    "MermaidRenderer",
    "TerminalCapabilities",
    "detect_terminal_capabilities",
    "render_mermaid",
]
