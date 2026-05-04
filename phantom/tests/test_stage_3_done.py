"""Stage 3 smoke test."""

from __future__ import annotations

import inspect

import pytest

from phantom.channels import (
    ChannelAdapter,
    ChannelEvent,
    ChannelMessage,
    ChannelRouter,
)
from phantom.channels.discord import DiscordAdapter
from phantom.channels.slack import SlackAdapter
from phantom.channels.telegram import TelegramAdapter
from phantom.channels.webchat import WebChatAdapter


@pytest.mark.stage3
def test_channel_adapter_is_abstract():
    assert inspect.isabstract(ChannelAdapter)


@pytest.mark.stage3
def test_webchat_inbound_event_round_trip():
    a = WebChatAdapter()
    a.connect()
    a.receive(user_id="alice", text="hello")
    e = a.next_event()
    assert isinstance(e, ChannelEvent)
    assert e.text == "hello"
    assert e.channel == "webchat"


@pytest.mark.stage3
def test_router_dispatches_outbound_message():
    a = WebChatAdapter()
    a.connect()
    r = ChannelRouter()
    r.register(a)
    msg = ChannelMessage(channel="webchat", reply_to="alice", text="ok")
    r.send(msg)
    assert a.sent == [msg]


@pytest.mark.stage3
def test_telegram_caps_trust_at_two():
    class _T:
        def get_updates(self, **kw): return []
        def send_message(self, **kw): return {}
    a = TelegramAdapter(token="tok", transport=_T())
    assert a.max_trust_level() == 2


@pytest.mark.stage3
def test_discord_caps_trust_at_two():
    class _T:
        def fetch_messages(self): return []
        def send_message(self, **kw): return {}
    a = DiscordAdapter(token="tok", transport=_T())
    assert a.max_trust_level() == 2


@pytest.mark.stage3
def test_slack_caps_trust_at_two():
    class _T:
        def fetch_events(self): return []
        def post_message(self, **kw): return {}
    a = SlackAdapter(bot_token="xoxb", transport=_T())
    assert a.max_trust_level() == 2


@pytest.mark.stage3
def test_phantom_stage_advanced_to_3_or_higher():
    import phantom
    assert phantom.feature_flags()["stage"] >= 3
