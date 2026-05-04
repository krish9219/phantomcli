"""Tests for ChannelEvent, ChannelMessage, ChannelRouter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from phantom.channels import (
    ChannelAdapter,
    ChannelEvent,
    ChannelMessage,
    ChannelRouter,
)
from phantom.errors import ChannelError


# ─── ChannelEvent ─────────────────────────────────────────────────────────────


class TestChannelEvent:
    def test_minimum_construction(self):
        e = ChannelEvent(channel="x", user_id="u", text="hello")
        assert e.channel == "x"
        assert e.text == "hello"
        assert e.received_at.tzinfo is not None

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            ChannelEvent(
                channel="x", user_id="u", text="hello",
                received_at=datetime(2026, 1, 1),  # noqa: DTZ001 — intentional
            )

    def test_immutable(self):
        e = ChannelEvent(channel="x", user_id="u", text="hello")
        with pytest.raises(Exception):
            e.text = "mutated"  # type: ignore[misc]


# ─── ChannelMessage ───────────────────────────────────────────────────────────


class TestChannelMessage:
    def test_minimum_construction(self):
        m = ChannelMessage(channel="x", reply_to="r", text="ok")
        assert m.parse_mode == "plain"
        assert m.extras == {}

    def test_immutable(self):
        m = ChannelMessage(channel="x", reply_to="r", text="ok")
        with pytest.raises(Exception):
            m.text = "mutated"  # type: ignore[misc]


# ─── ChannelRouter ────────────────────────────────────────────────────────────


class _FakeAdapter(ChannelAdapter):
    """Pure-Python adapter that records every call."""

    def __init__(self, name: str, *, cap: int = 2):
        self.name = name  # type: ignore[misc]
        self._cap = cap
        self.sent: list[ChannelMessage] = []
        self.connected = True

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def send(self, message: ChannelMessage) -> None:
        if not self.connected:
            raise ChannelError("disconnected")
        self.sent.append(message)

    def healthy(self) -> bool:
        return self.connected

    def max_trust_level(self) -> int:
        return self._cap


class TestChannelRouter:
    def test_register_and_get(self):
        r = ChannelRouter()
        a = _FakeAdapter("foo")
        r.register(a)
        assert r.names() == ["foo"]
        assert r.get("foo") is a

    def test_register_replaces_same_name(self):
        r = ChannelRouter()
        a1 = _FakeAdapter("foo")
        a2 = _FakeAdapter("foo")
        r.register(a1)
        r.register(a2)
        assert r.get("foo") is a2

    def test_unregister(self):
        r = ChannelRouter()
        r.register(_FakeAdapter("foo"))
        r.unregister("foo")
        assert r.names() == []
        # Idempotent.
        r.unregister("foo")

    def test_register_unnamed_adapter_rejected(self):
        r = ChannelRouter()
        a = _FakeAdapter("")
        with pytest.raises(ChannelError, match="no name"):
            r.register(a)

    def test_get_unknown_raises(self):
        r = ChannelRouter()
        with pytest.raises(ChannelError, match="no adapter registered"):
            r.get("missing")

    def test_send_routes_to_adapter(self):
        r = ChannelRouter()
        a = _FakeAdapter("foo")
        r.register(a)
        msg = ChannelMessage(channel="foo", reply_to="x", text="hi")
        r.send(msg)
        assert a.sent == [msg]

    def test_send_to_unknown_raises(self):
        r = ChannelRouter()
        with pytest.raises(ChannelError):
            r.send(ChannelMessage(channel="missing", reply_to="x", text="hi"))


class TestTrustClamping:
    def test_clamps_to_adapter_cap(self):
        r = ChannelRouter()
        r.register(_FakeAdapter("telegram", cap=2))
        e = ChannelEvent(channel="telegram", user_id="u", text="x")
        # User asked for trust 4 (God Mode) — telegram caps at 2.
        assert r.trust_for(e, 4) == 2

    def test_does_not_inflate(self):
        r = ChannelRouter()
        r.register(_FakeAdapter("webchat", cap=3))
        e = ChannelEvent(channel="webchat", user_id="u", text="x")
        assert r.trust_for(e, 1) == 1

    def test_unknown_channel_falls_back_to_paranoid(self):
        r = ChannelRouter()
        e = ChannelEvent(channel="ghost", user_id="u", text="x")
        assert r.trust_for(e, 4) == 1
