"""Tests for Discord + Slack adapters (parallel structure to Telegram)."""

from __future__ import annotations

from typing import Any

import pytest

from phantom.channels.discord import DiscordAdapter
from phantom.channels.message import ChannelMessage
from phantom.channels.slack import SlackAdapter
from phantom.errors import ChannelError


# ─── Discord ──────────────────────────────────────────────────────────────────


class _DiscordFake:
    def __init__(self, *, msgs: list[dict[str, Any]] | None = None):
        self._msgs = list(msgs or [])
        self.sent: list[dict[str, Any]] = []

    def fetch_messages(self) -> list[dict[str, Any]]:
        out, self._msgs = self._msgs, []
        return out

    def send_message(self, *, channel_id: str, text: str) -> dict[str, Any]:
        self.sent.append({"channel_id": channel_id, "text": text})
        return {"id": "x"}


class TestDiscordAdapter:
    def test_token_required(self):
        with pytest.raises(ChannelError):
            DiscordAdapter(token="", transport=_DiscordFake())

    def test_trust_cap_two(self):
        a = DiscordAdapter(token="tok", transport=_DiscordFake())
        assert a.max_trust_level() == 2

    def test_poll_and_event_translation(self):
        t = _DiscordFake(msgs=[{
            "id": "9", "author_id": "user-1", "channel_id": "chan-1",
            "guild_id": "guild-1", "content": "hi from discord",
        }])
        a = DiscordAdapter(token="tok", transport=t)
        a.connect()
        a.poll()
        e = a.next_event()
        assert e.text == "hi from discord"
        assert e.user_id == "user-1"
        assert e.reply_to == "chan-1"
        assert e.metadata["guild_id"] == "guild-1"
        assert a.next_event() is None

    def test_send_truncates_at_2000(self):
        t = _DiscordFake()
        a = DiscordAdapter(token="tok", transport=t)
        a.connect()
        a.send(ChannelMessage(channel="discord", reply_to="chan-1", text="x" * 3000))
        body = t.sent[0]["text"]
        assert len(body) <= 2000
        assert body.endswith("[…truncated]")

    def test_send_failure_wrapped(self):
        class _Boom:
            def fetch_messages(self): return []
            def send_message(self, **kw): raise OSError("net")
        a = DiscordAdapter(token="tok", transport=_Boom())
        a.connect()
        with pytest.raises(ChannelError, match="discord send failed"):
            a.send(ChannelMessage(channel="discord", reply_to="x", text="y"))


# ─── Slack ────────────────────────────────────────────────────────────────────


class _SlackFake:
    def __init__(self, *, events: list[dict[str, Any]] | None = None):
        self._events = list(events or [])
        self.sent: list[dict[str, Any]] = []

    def fetch_events(self) -> list[dict[str, Any]]:
        out, self._events = self._events, []
        return out

    def post_message(self, *, channel: str, text: str) -> dict[str, Any]:
        self.sent.append({"channel": channel, "text": text})
        return {"ok": True}


class TestSlackAdapter:
    def test_token_required(self):
        with pytest.raises(ChannelError):
            SlackAdapter(bot_token="", transport=_SlackFake())

    def test_trust_cap_two(self):
        a = SlackAdapter(bot_token="xoxb", transport=_SlackFake())
        assert a.max_trust_level() == 2

    def test_poll_translates_message_events_only(self):
        t = _SlackFake(events=[
            {"type": "message", "user": "U1", "channel": "C1",
             "text": "hi", "team": "T1", "ts": "1.2", "thread_ts": None},
            {"type": "reaction_added", "user": "U2"},  # ignored
        ])
        a = SlackAdapter(bot_token="xoxb", transport=t)
        a.connect()
        a.poll()
        e = a.next_event()
        assert e.text == "hi"
        assert e.user_id == "U1"
        assert e.reply_to == "C1"
        assert e.metadata["team"] == "T1"
        assert a.next_event() is None

    def test_send_truncates_at_4000(self):
        t = _SlackFake()
        a = SlackAdapter(bot_token="xoxb", transport=t)
        a.connect()
        a.send(ChannelMessage(channel="slack", reply_to="C1", text="x" * 5000))
        body = t.sent[0]["text"]
        assert len(body) <= 4000
        assert body.endswith("[…truncated]")

    def test_send_failure_wrapped(self):
        class _Boom:
            def fetch_events(self): return []
            def post_message(self, **kw): raise OSError("net")
        a = SlackAdapter(bot_token="xoxb", transport=_Boom())
        a.connect()
        with pytest.raises(ChannelError, match="slack send failed"):
            a.send(ChannelMessage(channel="slack", reply_to="x", text="y"))
