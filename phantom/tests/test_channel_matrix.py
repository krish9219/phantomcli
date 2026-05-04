"""Tests for the Matrix channel adapter.

Adapter is exercised against an in-memory fake transport. The
HttpxMatrixTransport's wire format is exercised against a fake httpx
client that returns canned dict bodies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from phantom.channels.matrix import MatrixAdapter
from phantom.channels.matrix.transport import HttpxMatrixTransport
from phantom.channels.message import ChannelMessage
from phantom.errors import ChannelError


# ─── fake transport for adapter tests ───────────────────────────────────────


class _FakeTransport:
    def __init__(self, *, events: list[dict[str, Any]] | None = None):
        self._events = list(events or [])
        self.sent: list[dict[str, Any]] = []

    def fetch_events(self) -> list[dict[str, Any]]:
        out, self._events = self._events, []
        return out

    def send_message(self, *, room_id: str, body: str) -> dict[str, Any]:
        self.sent.append({"room_id": room_id, "body": body})
        return {"event_id": "$mock"}


# ─── adapter behaviour ──────────────────────────────────────────────────────


def test_adapter_requires_transport():
    with pytest.raises(ChannelError):
        MatrixAdapter(transport=None)  # type: ignore[arg-type]


def test_adapter_starts_disconnected():
    a = MatrixAdapter(transport=_FakeTransport())
    assert not a.healthy()
    assert a.next_event() is None


def test_adapter_connect_makes_healthy():
    a = MatrixAdapter(transport=_FakeTransport())
    a.connect()
    assert a.healthy()


def test_adapter_close_idempotent():
    a = MatrixAdapter(transport=_FakeTransport())
    a.connect()
    a.close()
    a.close()  # no exception


def test_adapter_max_trust_level_is_2():
    a = MatrixAdapter(transport=_FakeTransport())
    assert a.max_trust_level() == 2


def test_adapter_yields_text_messages():
    fake = _FakeTransport(events=[
        {
            "type": "m.room.message",
            "sender": "@alice:matrix.org",
            "room_id": "!room1:matrix.org",
            "content": {"msgtype": "m.text", "body": "hello phantom"},
            "origin_server_ts": 1715000000000,
        },
    ])
    a = MatrixAdapter(transport=fake)
    a.connect()
    ev = a.next_event()
    assert ev is not None
    assert ev.channel == "matrix"
    assert ev.user_id == "@alice:matrix.org"
    assert ev.metadata.get("room_id") == "!room1:matrix.org"
    assert ev.text == "hello phantom"
    assert isinstance(ev.received_at, datetime)
    assert ev.received_at.tzinfo == timezone.utc


def test_adapter_skips_own_messages():
    fake = _FakeTransport(events=[
        {
            "type": "m.room.message", "sender": "@bot:matrix.org",
            "content": {"msgtype": "m.text", "body": "echo"},
        },
        {
            "type": "m.room.message", "sender": "@alice:matrix.org",
            "content": {"msgtype": "m.text", "body": "real"},
        },
    ])
    a = MatrixAdapter(transport=fake, user_id="@bot:matrix.org")
    a.connect()
    ev = a.next_event()
    assert ev is not None
    assert ev.text == "real"
    assert a.next_event() is None


def test_adapter_skips_non_text_events():
    fake = _FakeTransport(events=[
        {
            "type": "m.room.message", "sender": "@alice:matrix.org",
            "content": {"msgtype": "m.image", "body": "image.png", "url": "mxc://x"},
        },
        {
            "type": "m.room.member", "sender": "@alice:matrix.org",
            "content": {"membership": "join"},
        },
        {
            "type": "m.room.message", "sender": "@alice:matrix.org",
            "content": {"msgtype": "m.text", "body": "yes"},
        },
    ])
    a = MatrixAdapter(transport=fake)
    a.connect()
    ev = a.next_event()
    assert ev is not None and ev.text == "yes"
    assert a.next_event() is None


def test_adapter_handles_m_notice():
    fake = _FakeTransport(events=[
        {
            "type": "m.room.message", "sender": "@bot2:matrix.org",
            "content": {"msgtype": "m.notice", "body": "FYI"},
        },
    ])
    a = MatrixAdapter(transport=fake)
    a.connect()
    ev = a.next_event()
    assert ev is not None and ev.text == "FYI"


def test_adapter_send_requires_room_id():
    a = MatrixAdapter(transport=_FakeTransport())
    a.connect()
    msg = ChannelMessage(channel="matrix", reply_to="", text="hi")
    with pytest.raises(ChannelError, match="room_id"):
        a.send(msg)


def test_adapter_send_when_disconnected():
    a = MatrixAdapter(transport=_FakeTransport())
    msg = ChannelMessage(channel="matrix", reply_to="!r:m", text="hi")
    with pytest.raises(ChannelError, match="not connected"):
        a.send(msg)


def test_adapter_send_dispatches_to_transport():
    fake = _FakeTransport()
    a = MatrixAdapter(transport=fake)
    a.connect()
    msg = ChannelMessage(channel="matrix", reply_to="", text="hi",
                         extras={"room_id": "!room:matrix.org"})
    a.send(msg)
    assert fake.sent == [{"room_id": "!room:matrix.org", "body": "hi"}]


def test_adapter_send_falls_back_to_reply_to():
    fake = _FakeTransport()
    a = MatrixAdapter(transport=fake)
    a.connect()
    msg = ChannelMessage(channel="matrix", reply_to="!fallback:m", text="hi")
    a.send(msg)
    assert fake.sent == [{"room_id": "!fallback:m", "body": "hi"}]


def test_adapter_transport_exception_wrapped():
    class _Boom:
        def fetch_events(self): raise RuntimeError("net down")
        def send_message(self, **kw): raise RuntimeError("net down")
    a = MatrixAdapter(transport=_Boom())  # type: ignore[arg-type]
    a.connect()
    with pytest.raises(ChannelError, match="matrix fetch"):
        a.next_event()


# ─── HttpxMatrixTransport (against fake httpx client) ───────────────────────


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self) -> dict:
        return self._body


class _FakeHttpx:
    def __init__(self, *, get_response: _FakeResponse | None = None,
                 put_response: _FakeResponse | None = None):
        self._get = get_response
        self._put = put_response
        self.gets: list[tuple[str, dict, dict]] = []
        self.puts: list[tuple[str, dict, dict]] = []

    def get(self, url, *, headers=None, params=None):
        self.gets.append((url, headers or {}, params or {}))
        return self._get or _FakeResponse(body={"next_batch": "x", "rooms": {"join": {}}})

    def put(self, url, *, headers=None, json=None):
        self.puts.append((url, headers or {}, json or {}))
        return self._put or _FakeResponse(body={"event_id": "$y"})


def test_transport_requires_homeserver_and_token():
    with pytest.raises(ChannelError):
        HttpxMatrixTransport(homeserver_url="", access_token="t")
    with pytest.raises(ChannelError):
        HttpxMatrixTransport(homeserver_url="https://m", access_token="")


def test_transport_fetch_events_uses_sync_endpoint():
    fake = _FakeHttpx(get_response=_FakeResponse(body={
        "next_batch": "tok-1",
        "rooms": {"join": {"!r1:m": {"timeline": {"events": [
            {"type": "m.room.message", "sender": "@a:m",
             "content": {"msgtype": "m.text", "body": "hi"}},
        ]}}}},
    }))
    t = HttpxMatrixTransport(
        homeserver_url="https://matrix.example", access_token="tok",
        client=fake,
    )
    events = t.fetch_events()
    assert len(events) == 1
    assert events[0]["room_id"] == "!r1:m"
    assert events[0]["content"]["body"] == "hi"
    # URL hits /_matrix/client/v3/sync
    assert "/sync" in fake.gets[0][0]


def test_transport_filters_to_configured_rooms():
    fake = _FakeHttpx(get_response=_FakeResponse(body={
        "next_batch": "x",
        "rooms": {"join": {
            "!allowed:m": {"timeline": {"events": [
                {"type": "m.room.message", "sender": "@a:m",
                 "content": {"msgtype": "m.text", "body": "yes"}},
            ]}},
            "!other:m": {"timeline": {"events": [
                {"type": "m.room.message", "sender": "@b:m",
                 "content": {"msgtype": "m.text", "body": "no"}},
            ]}},
        }},
    }))
    t = HttpxMatrixTransport(
        homeserver_url="https://m", access_token="tok",
        rooms=["!allowed:m"], client=fake,
    )
    events = t.fetch_events()
    assert len(events) == 1
    assert events[0]["room_id"] == "!allowed:m"


def test_transport_persists_next_batch():
    fake = _FakeHttpx(get_response=_FakeResponse(body={
        "next_batch": "tok-99", "rooms": {"join": {}},
    }))
    t = HttpxMatrixTransport(
        homeserver_url="https://m", access_token="tok", client=fake,
    )
    t.fetch_events()
    fake._get = _FakeResponse(body={"next_batch": "tok-100", "rooms": {"join": {}}})
    t.fetch_events()
    assert fake.gets[1][2].get("since") == "tok-99"


def test_transport_send_message_uses_put_with_txn_id():
    fake = _FakeHttpx()
    t = HttpxMatrixTransport(
        homeserver_url="https://m", access_token="tok", client=fake,
    )
    t.send_message(room_id="!r:m", body="hello")
    assert len(fake.puts) == 1
    url, _hdr, body = fake.puts[0]
    assert "/rooms/!r:m/send/m.room.message/phantom-" in url
    assert body == {"msgtype": "m.text", "body": "hello"}


def test_transport_http_error_wrapped():
    fake = _FakeHttpx(get_response=_FakeResponse(status_code=503, text="overloaded"))
    t = HttpxMatrixTransport(
        homeserver_url="https://m", access_token="tok", client=fake,
    )
    with pytest.raises(ChannelError, match="503"):
        t.fetch_events()


def test_transport_send_http_error_wrapped():
    fake = _FakeHttpx(put_response=_FakeResponse(status_code=403, text="forbidden"))
    t = HttpxMatrixTransport(
        homeserver_url="https://m", access_token="tok", client=fake,
    )
    with pytest.raises(ChannelError, match="403"):
        t.send_message(room_id="!r:m", body="x")
