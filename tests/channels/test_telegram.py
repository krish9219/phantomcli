"""Tests for the Telegram adapter."""

from __future__ import annotations

from typing import Any

import pytest

from phantom.channels.message import ChannelMessage
from phantom.channels.telegram import TelegramAdapter
from phantom.errors import ChannelError


class _FakeTransport:
    def __init__(self, *, updates: list[dict[str, Any]] | None = None):
        self._updates = list(updates or [])
        self.sent: list[dict[str, Any]] = []
        self.last_offset: int | None = None

    def get_updates(self, *, offset: int, timeout: float) -> list[dict[str, Any]]:
        self.last_offset = offset
        # Return queued, clear; subsequent polls return [].
        out, self._updates = self._updates, []
        return out

    def send_message(self, *, chat_id: str, text: str) -> dict[str, Any]:
        self.sent.append({"chat_id": chat_id, "text": text})
        return {"ok": True}


class TestTelegramAdapter:
    def test_token_required(self):
        with pytest.raises(ChannelError, match="non-empty token"):
            TelegramAdapter(token="", transport=_FakeTransport())

    def test_max_trust_is_two(self):
        a = TelegramAdapter(token="tok", transport=_FakeTransport())
        assert a.max_trust_level() == 2

    def test_lifecycle(self):
        a = TelegramAdapter(token="tok", transport=_FakeTransport())
        assert not a.healthy()
        a.connect()
        assert a.healthy()
        a.close()
        assert not a.healthy()
        a.close()

    def test_poll_creates_events(self):
        t = _FakeTransport(updates=[
            {
                "update_id": 1,
                "message": {
                    "from": {"id": 42},
                    "chat": {"id": 100},
                    "text": "hello",
                },
            },
            {
                "update_id": 2,
                "message": {
                    "from": {"id": 43},
                    "chat": {"id": 100},
                    "text": "world",
                },
            },
        ])
        a = TelegramAdapter(token="tok", transport=t)
        a.connect()
        a.poll()
        e1 = a.next_event()
        e2 = a.next_event()
        assert e1.text == "hello" and e1.user_id == "42" and e1.reply_to == "100"
        assert e2.text == "world"
        assert a.next_event() is None
        # offset advanced past the highest update_id.
        a.poll()
        assert t.last_offset == 3

    def test_poll_skips_non_message_updates(self):
        t = _FakeTransport(updates=[
            {"update_id": 1, "edited_message": {"text": "edit"}},  # ignored
            {"update_id": 2, "message": {"from": {"id": 1}, "chat": {"id": 9}, "text": "real"}},
        ])
        a = TelegramAdapter(token="tok", transport=t)
        a.connect()
        a.poll()
        e = a.next_event()
        assert e.text == "real"
        assert a.next_event() is None

    def test_poll_when_disconnected_raises(self):
        a = TelegramAdapter(token="tok", transport=_FakeTransport())
        with pytest.raises(ChannelError, match="not connected"):
            a.poll()

    def test_send_when_disconnected_raises(self):
        a = TelegramAdapter(token="tok", transport=_FakeTransport())
        with pytest.raises(ChannelError, match="not connected"):
            a.send(ChannelMessage(channel="telegram", reply_to="100", text="x"))

    def test_send_for_wrong_channel_raises(self):
        a = TelegramAdapter(token="tok", transport=_FakeTransport())
        a.connect()
        with pytest.raises(ChannelError, match="telegram adapter"):
            a.send(ChannelMessage(channel="discord", reply_to="x", text="y"))

    def test_send_truncates_oversize(self):
        t = _FakeTransport()
        a = TelegramAdapter(token="tok", transport=t)
        a.connect()
        big = "x" * 5000
        a.send(ChannelMessage(channel="telegram", reply_to="100", text=big))
        body = t.sent[0]["text"]
        assert len(body) <= 4096
        assert body.endswith("[…truncated]")

    def test_send_under_limit_passes_through(self):
        t = _FakeTransport()
        a = TelegramAdapter(token="tok", transport=t)
        a.connect()
        a.send(ChannelMessage(channel="telegram", reply_to="100", text="hi"))
        assert t.sent == [{"chat_id": "100", "text": "hi"}]

    def test_send_failure_wrapped_in_channel_error(self):
        class _Boom:
            def get_updates(self, **kw): return []
            def send_message(self, **kw): raise RuntimeError("boom")
        a = TelegramAdapter(token="tok", transport=_Boom())
        a.connect()
        with pytest.raises(ChannelError, match="telegram send failed"):
            a.send(ChannelMessage(channel="telegram", reply_to="100", text="x"))
