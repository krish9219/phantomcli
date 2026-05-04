"""Phantom TUI polish layer.

Pieces
------

* :class:`StreamingResponse` — buffered streaming printer that flushes
  to a :class:`rich.live.Live` panel without flicker.
* :class:`ProgressTracker` — context-manager progress widget for
  long-running ops (build, swarm, self-dev).
* :class:`FileUpdateSidePanel` — running list of files the agent has
  touched in the current session, rendered as a :class:`rich.panel.Panel`
  on demand.

Each piece is built on Rich. They expose pure functions that compose
:class:`rich.console.Console`-renderable objects so tests can assert on
the rendered text without spinning a real terminal.
"""

from __future__ import annotations

from phantom.tui.file_panel import FileUpdate, FileUpdateSidePanel
from phantom.tui.progress import ProgressTracker
from phantom.tui.streaming import StreamingResponse, render_token

__all__ = [
    "FileUpdate",
    "FileUpdateSidePanel",
    "ProgressTracker",
    "StreamingResponse",
    "render_token",
]
