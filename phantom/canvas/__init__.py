"""Phantom canvas — server-side rendering surface for structured UI.

The agent emits :class:`CanvasNode` trees; the dashboard renders them.
The wire format is a stable JSON shape so a future native renderer can
consume the same protocol.

Supported node kinds (Stage 6):

* ``text``     — paragraph or heading.
* ``code``     — fenced code block with language hint.
* ``table``    — header row + data rows.
* ``chart``    — time-series or bar; SVG rendered client-side.
* ``button``   — clickable, emits an event back to the agent.
* ``form``     — input + submit; emits a structured event.

A node may carry children (typed as :class:`CanvasNode`).
"""

from __future__ import annotations

from phantom.canvas.node import CanvasNode, render_to_dict
from phantom.canvas.render import render_to_html

__all__ = ["CanvasNode", "render_to_dict", "render_to_html"]
