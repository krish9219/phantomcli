"""Phantom dashboard — the web front-end users actually open in a browser.

The dashboard is a FastAPI app + a vanilla-JS single-page front-end.
It consumes the Stage-3 :class:`WebChatAdapter` for chat traffic, the
Stage-6 :func:`render_to_html` for canvas rendering, and the Stage-4
:class:`AgentSession` for the conversation loop.

Bind locally — the default :func:`build_app` listens on 127.0.0.1.
Operators who need LAN/WAN access put Caddy in front; never expose
the FastAPI port directly.
"""

from __future__ import annotations

from phantom.dashboard.server import DashboardConfig, build_app

__all__ = ["DashboardConfig", "build_app"]
