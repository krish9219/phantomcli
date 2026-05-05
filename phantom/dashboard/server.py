"""FastAPI app for the Phantom dashboard.

Routes
------

* ``GET  /``            — the SPA shell (``index.html``).
* ``GET  /static/<f>``  — the JS/CSS/icons.
* ``GET  /api/health``  — liveness + version.
* ``GET  /api/plugins`` — discovered plugins (manifest + enabled flag).
* ``GET  /api/memory``  — recent memory records (per-namespace).
* ``WS   /ws/chat``     — bidirectional chat with the agent.

Wire format on ``/ws/chat``
---------------------------

Inbound (browser → server)::

    {"type": "user_message", "text": "hello"}
    {"type": "ping"}

Outbound (server → browser)::

    {"type": "assistant_message", "text": "...", "html": "<div>...</div>"}
    {"type": "system", "text": "...", "level": "info" | "warn" | "error"}
    {"type": "pong"}

The server renders the assistant text once into HTML using
:func:`phantom.canvas.render_to_html` (when the response is a canvas
tree) or escapes it with the same library (when it's plain text). The
browser does not parse or re-render — it injects HTML directly.

Security
--------

* Loopback bind by default (``127.0.0.1``). LAN exposure requires
  setting ``host="0.0.0.0"`` *and* fronting with TLS + auth (Caddy).
* CSP header forbids inline scripts (the static ``main.js`` is the
  only JS we serve; no eval, no inline ``<script>`` blocks).
* No CORS by default; the SPA and API share an origin.
* Per-WebSocket message-size cap (16 KiB) defends against memory
  exhaustion from a hostile client.
"""

# NOTE: deliberately NO `from __future__ import annotations` here.
# FastAPI introspects function signatures at decorator time to decide
# whether a parameter is a path/query/body argument or one of the
# framework's special types (Request, WebSocket). PEP-563 deferred
# evaluation breaks that — FastAPI sees the string "WebSocket" and
# treats `ws` as a missing query parameter. The other phantom modules
# keep `from __future__ import annotations`; this one cannot.

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from phantom._version import __version__
from phantom.agent import AgentSession, ScriptedProvider, default_tools
from phantom.canvas import CanvasNode, render_to_html
from phantom.channels.webchat import WebChatAdapter
from phantom.errors import PhantomError

__all__ = ["DashboardConfig", "build_app"]

log = logging.getLogger(__name__)


_STATIC_DIR = Path(__file__).resolve().parent / "static"
_MAX_WS_MESSAGE_BYTES = 16 * 1024


@dataclass
class DashboardConfig:
    """Construction-time wiring for the dashboard.

    Attributes
    ----------
    session_factory:
        Returns a fresh :class:`AgentSession` for each WebSocket
        connection. Defaults to a tools-less ScriptedProvider session
        that echoes "(no provider configured)" — safe to deploy
        without an LLM.
    plugin_provider:
        Returns the list-of-dicts the ``/api/plugins`` endpoint
        serves. None ⇒ empty list.
    memory_provider:
        ``(user, project, session) -> list[dict]`` for the
        ``/api/memory`` endpoint.
    enable_csp:
        Set False only in tests where the strict CSP would block
        DevTools.
    """

    session_factory: Callable[[], AgentSession] = field(
        default_factory=lambda: _default_session_factory,
    )
    plugin_provider: Callable[[], list[dict[str, Any]]] | None = None
    memory_provider: Callable[[str, str, str], list[dict[str, Any]]] | None = None
    enable_csp: bool = True


def _default_session_factory() -> AgentSession:
    """Echo session — every user turn yields a constant assistant turn.

    Used when an operator runs the dashboard without a configured
    provider. Replace for real chat.
    """
    from phantom.agent.provider import ProviderResponse
    return AgentSession(
        provider=ScriptedProvider(_responses=[
            ProviderResponse(text=(
                "Phantom dashboard is running but no provider is "
                "configured. Pass `--base-url` and `--model` to `phantom "
                "chat`, or wire a session_factory in DashboardConfig."
            )),
        ] * 100),
        tools=[],
    )


def build_app(config: DashboardConfig | None = None) -> Any:
    """Create and return the FastAPI app.

    Importing this function does **not** import FastAPI; the import
    happens here so that callers who never construct the app pay
    nothing at startup.
    """
    from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.websockets import WebSocketDisconnect

    cfg = config or DashboardConfig()

    app = FastAPI(
        title="Phantom",
        version=__version__,
        docs_url=None,    # we hide the swagger UI; this is not an API product
        redoc_url=None,
    )

    # ─── static files ──────────────────────────────────────────────────
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # ─── CSP middleware ────────────────────────────────────────────────
    if cfg.enable_csp:
        @app.middleware("http")
        async def _csp(request, call_next):
            resp = await call_next(request)
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "frame-ancestors 'none'"
            )
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["X-Frame-Options"] = "DENY"
            resp.headers["Referrer-Policy"] = "no-referrer"
            return resp

    # ─── routes ────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def _root():
        index = _STATIC_DIR / "index.html"
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @app.get("/api/health")
    async def _health():
        return {
            "ok": True,
            "version": __version__,
            "service": "phantom-dashboard",
        }

    @app.get("/api/plugins")
    async def _plugins():
        if cfg.plugin_provider is None:
            return []
        try:
            return cfg.plugin_provider()
        except Exception as exc:
            log.exception("plugin_provider failed: %s", exc)
            raise HTTPException(500, "plugin discovery failed")

    @app.get("/api/memory")
    async def _memory(
        user: str = Query(..., min_length=1, max_length=64),
        project: str = Query(..., min_length=1, max_length=64),
        session: str = Query(..., min_length=1, max_length=64),
    ):
        if cfg.memory_provider is None:
            return []
        try:
            return cfg.memory_provider(user, project, session)
        except Exception as exc:
            log.exception("memory_provider failed: %s", exc)
            raise HTTPException(500, "memory query failed")

    # ─── WebSocket chat ────────────────────────────────────────────────
    @app.websocket("/ws/chat")
    async def _ws_chat(ws: WebSocket):
        await ws.accept()
        adapter = WebChatAdapter()
        adapter.connect()
        session = cfg.session_factory()
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > _MAX_WS_MESSAGE_BYTES:
                    await ws.send_json({
                        "type": "system", "level": "error",
                        "text": "message too large",
                    })
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({
                        "type": "system", "level": "error",
                        "text": "invalid JSON",
                    })
                    continue
                kind = msg.get("type")
                if kind == "ping":
                    await ws.send_json({"type": "pong"})
                    continue
                if kind == "user_message":
                    text = msg.get("text", "")
                    if not isinstance(text, str) or not text.strip():
                        await ws.send_json({
                            "type": "system", "level": "warn",
                            "text": "empty message",
                        })
                        continue
                    try:
                        reply = session.respond_to(text)
                    except PhantomError as exc:
                        await ws.send_json({
                            "type": "system", "level": "error",
                            "text": exc.detail or str(exc),
                        })
                        continue
                    rendered = _render_reply(reply)
                    await ws.send_json({
                        "type": "assistant_message",
                        "text": reply,
                        "html": rendered,
                    })
                else:
                    await ws.send_json({
                        "type": "system", "level": "warn",
                        "text": f"unknown message type {kind!r}",
                    })
        except WebSocketDisconnect:
            return
        finally:
            adapter.close()

    return app


def _render_reply(reply: str) -> str:
    """Render a reply as HTML.

    Strategy: if the reply is a JSON document representing a
    :class:`CanvasNode`, render the tree. Otherwise treat as plain
    text and HTML-escape via the canvas text-node renderer.
    """
    stripped = reply.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
            node = _dict_to_canvas(data)
            return render_to_html(node)
        except (json.JSONDecodeError, PhantomError, KeyError, TypeError):
            # Fall through to plain-text rendering.
            pass
    return render_to_html(CanvasNode(kind="text", props={"value": reply}))


def _dict_to_canvas(data: dict[str, Any]) -> CanvasNode:
    """Convert a JSON-shaped dict (the output of ``render_to_dict``) into
    a :class:`CanvasNode`."""
    if not isinstance(data, dict) or "kind" not in data:
        raise PhantomError("not a canvas tree")
    children = tuple(
        _dict_to_canvas(c) for c in data.get("children", []) or ()
    )
    return CanvasNode(
        kind=data["kind"],
        props=data.get("props", {}),
        children=children,
    )
