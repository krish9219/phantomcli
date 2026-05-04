"""Tests for the WebChat adapter."""

from __future__ import annotations

import pytest

from phantom.channels.message import ChannelMessage
from phantom.channels.webchat import WebChatAdapter
from phantom.errors import ChannelError


class TestWebChatAdapter:
    def test_name_and_trust(self):
        a = WebChatAdapter()
        assert a.name == "webchat"
        assert a.max_trust_level() == 3  # local user

    def test_lifecycle(self):
        a = WebChatAdapter()
        assert not a.healthy()
        a.connect()
        assert a.healthy()
        a.close()
        assert not a.healthy()
        # Idempotent close.
        a.close()

    def test_receive_then_next_event(self):
        a = WebChatAdapter()
        a.connect()
        a.receive(user_id="alice", text="hello")
        evt = a.next_event()
        assert evt is not None
        assert evt.channel == "webchat"
        assert evt.user_id == "alice"
        assert evt.text == "hello"
        assert a.next_event() is None

    def test_receive_when_disconnected_raises(self):
        a = WebChatAdapter()
        with pytest.raises(ChannelError, match="not connected"):
            a.receive(user_id="alice", text="hi")

    def test_send_records_when_no_callback(self):
        a = WebChatAdapter()
        a.connect()
        msg = ChannelMessage(channel="webchat", reply_to="alice", text="ok")
        a.send(msg)
        assert a.sent == [msg]

    def test_send_invokes_callback(self):
        captured: list[ChannelMessage] = []
        a = WebChatAdapter(outbound=captured.append)
        a.connect()
        msg = ChannelMessage(channel="webchat", reply_to="alice", text="ok")
        a.send(msg)
        assert captured == [msg]

    def test_send_when_disconnected_raises(self):
        a = WebChatAdapter()
        with pytest.raises(ChannelError, match="not connected"):
            a.send(ChannelMessage(channel="webchat", reply_to="x", text="y"))

    def test_send_for_wrong_channel_raises(self):
        a = WebChatAdapter()
        a.connect()
        with pytest.raises(ChannelError, match="webchat adapter"):
            a.send(ChannelMessage(channel="telegram", reply_to="x", text="y"))

    def test_close_clears_inbox(self):
        a = WebChatAdapter()
        a.connect()
        a.receive(user_id="u", text="x")
        a.close()
        assert a.next_event() is None
