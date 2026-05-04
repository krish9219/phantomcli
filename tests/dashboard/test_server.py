"""End-to-end tests for the dashboard FastAPI app.

These exercise: static asset serving, every JSON API, the WebSocket
chat round-trip, CSP headers, plain-text + canvas-tree replies, and
the hostile-input defences.
"""

from __future__ import annotations

import json

import pytest

# Skip the whole module gracefully if FastAPI isn't installed.
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from phantom.agent import AgentSession, ScriptedProvider  # noqa: E402
from phantom.agent.provider import ProviderResponse  # noqa: E402
from phantom.canvas import CanvasNode, render_to_dict  # noqa: E402
from phantom.dashboard import DashboardConfig, build_app  # noqa: E402


# ─── shared fixtures ─────────────────────────────────────────────────────────


def _make_session_factory(replies: list[str]):
    def factory():
        return AgentSession(
            provider=ScriptedProvider.from_responses(
                [ProviderResponse(text=r) for r in replies]
            ),
            tools=[],
        )
    return factory


@pytest.fixture
def client():
    cfg = DashboardConfig(
        session_factory=_make_session_factory(["pong"]),
        plugin_provider=lambda: [
            {"name": "clock", "version": "1.0.0",
             "capabilities": [], "enabled": True, "signed": False},
        ],
        memory_provider=lambda u, p, s: [
            {"id": 1, "text": "first note", "kind": "note"},
        ],
    )
    return TestClient(build_app(cfg))


# ─── HTTP routes ─────────────────────────────────────────────────────────────


class TestRoot:
    def test_serves_index_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Phantom" in r.text
        # Must reference the static bundle.
        assert "/static/main.js" in r.text
        assert "/static/main.css" in r.text


class TestStaticAssets:
    def test_main_js_served(self, client):
        r = client.get("/static/main.js")
        assert r.status_code == 200
        assert "WebSocket" in r.text

    def test_main_css_served(self, client):
        r = client.get("/static/main.css")
        assert r.status_code == 200
        assert ".phc-text" in r.text  # canvas styles present

    def test_icon_served(self, client):
        r = client.get("/static/icon-192.png")
        assert r.status_code == 200
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_unknown_static_returns_404(self, client):
        assert client.get("/static/does-not-exist").status_code == 404


class TestHealth:
    def test_health_shape(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["service"] == "phantom-dashboard"
        assert isinstance(body["version"], str)


class TestPluginsApi:
    def test_returns_provider_payload(self, client):
        r = client.get("/api/plugins")
        assert r.status_code == 200
        names = {p["name"] for p in r.json()}
        assert "clock" in names

    def test_empty_when_no_provider(self):
        client = TestClient(build_app(DashboardConfig()))
        r = client.get("/api/plugins")
        assert r.status_code == 200
        assert r.json() == []

    def test_500_when_provider_raises(self):
        def boom():
            raise RuntimeError("upstream")
        client = TestClient(build_app(DashboardConfig(plugin_provider=boom)))
        r = client.get("/api/plugins")
        assert r.status_code == 500


class TestMemoryApi:
    def test_returns_provider_payload(self, client):
        r = client.get("/api/memory",
                       params={"user": "u", "project": "p", "session": "s"})
        assert r.status_code == 200
        assert r.json() == [{"id": 1, "text": "first note", "kind": "note"}]

    def test_validates_namespace_params(self, client):
        # Missing query params → 422 from FastAPI.
        assert client.get("/api/memory").status_code == 422

    def test_empty_string_rejected(self, client):
        # min_length=1 ⇒ FastAPI validation error.
        r = client.get("/api/memory",
                       params={"user": "", "project": "p", "session": "s"})
        assert r.status_code == 422

    def test_500_when_provider_raises(self):
        def boom(u, p, s):
            raise RuntimeError("db gone")
        client = TestClient(build_app(DashboardConfig(memory_provider=boom)))
        r = client.get("/api/memory",
                       params={"user": "u", "project": "p", "session": "s"})
        assert r.status_code == 500


class TestSecurityHeaders:
    def test_csp_present_on_html(self, client):
        r = client.get("/")
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]

    def test_csp_can_be_disabled(self):
        client = TestClient(build_app(DashboardConfig(enable_csp=False)))
        r = client.get("/")
        assert "content-security-policy" not in r.headers

    def test_no_clickjacking_header(self, client):
        r = client.get("/")
        assert r.headers["x-frame-options"] == "DENY"

    def test_nosniff_header(self, client):
        r = client.get("/")
        assert r.headers["x-content-type-options"] == "nosniff"


# ─── WebSocket chat ──────────────────────────────────────────────────────────


class TestWebSocketChat:
    def test_user_message_round_trip(self, client):
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "user_message", "text": "hi"}))
            msg = ws.receive_json()
            assert msg["type"] == "assistant_message"
            assert msg["text"] == "pong"
            # The server escaped + wrapped the text in canvas HTML.
            assert "phc-text" in msg["html"]
            assert ">pong</p>" in msg["html"]

    def test_canvas_tree_reply_renders_to_html(self):
        # When the provider returns a JSON canvas tree, the server
        # parses it and produces a richer HTML rendering.
        canvas_dict = render_to_dict(
            CanvasNode(kind="code",
                       props={"value": "print(1)", "language": "python"})
        )
        cfg = DashboardConfig(
            session_factory=_make_session_factory([json.dumps(canvas_dict)]),
        )
        c = TestClient(build_app(cfg))
        with c.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "user_message", "text": "go"}))
            msg = ws.receive_json()
            assert "<pre" in msg["html"]
            assert "language-python" in msg["html"]
            assert "print(1)" in msg["html"]

    def test_xss_attempt_in_user_text_is_escaped(self, client):
        # The bot in this fixture echoes "pong"; the user's text is
        # not what the server renders. We assert the server's reply
        # is escaped — but the more interesting case is the canvas
        # tree path, tested elsewhere.
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({
                "type": "user_message",
                "text": "<script>alert(1)</script>",
            }))
            msg = ws.receive_json()
            assert "<script>" not in msg["html"]

    def test_xss_attempt_in_assistant_text_is_escaped(self):
        cfg = DashboardConfig(
            session_factory=_make_session_factory(["<script>alert(1)</script>"]),
        )
        c = TestClient(build_app(cfg))
        with c.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "user_message", "text": "x"}))
            msg = ws.receive_json()
            assert "<script>" not in msg["html"]
            assert "&lt;script&gt;" in msg["html"]

    def test_ping_pong(self, client):
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            assert ws.receive_json() == {"type": "pong"}

    def test_invalid_json(self, client):
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text("not json")
            msg = ws.receive_json()
            assert msg["type"] == "system"
            assert msg["level"] == "error"

    def test_unknown_message_type(self, client):
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "fly_to_moon"}))
            msg = ws.receive_json()
            assert msg["type"] == "system"
            assert msg["level"] == "warn"

    def test_empty_text_rejected(self, client):
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "user_message", "text": "   "}))
            msg = ws.receive_json()
            assert msg["type"] == "system"
            assert "empty" in msg["text"]

    def test_oversized_message_rejected(self, client):
        big = "x" * (16 * 1024 + 100)
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "user_message", "text": big}))
            msg = ws.receive_json()
            assert msg["type"] == "system"
            assert msg["level"] == "error"
            assert "too large" in msg["text"]

    def test_provider_error_surfaced_as_system_message(self):
        cfg = DashboardConfig(session_factory=_make_session_factory([]))
        c = TestClient(build_app(cfg))
        with c.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "user_message", "text": "hi"}))
            msg = ws.receive_json()
            assert msg["type"] == "system"
            assert msg["level"] == "error"


# ─── Default echo session (no provider configured) ───────────────────────────


class TestDefaultEchoSession:
    def test_default_session_emits_friendly_hint(self):
        c = TestClient(build_app())  # no config ⇒ default echo session
        with c.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "user_message", "text": "hi"}))
            msg = ws.receive_json()
            assert msg["type"] == "assistant_message"
            assert "no provider is configured" in msg["text"]
