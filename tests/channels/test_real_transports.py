"""Real-shape tests for the httpx transports.

We use httpx.MockTransport to assert the transports send the right
payloads and parse the right responses. No live network calls.
"""

from __future__ import annotations

import json

import httpx
import pytest

from phantom.channels.discord.transport import HttpxDiscordTransport
from phantom.channels.slack.transport import HttpxSlackTransport
from phantom.channels.telegram.transport import HttpxTelegramTransport
from phantom.errors import ChannelError


# ─── Telegram ────────────────────────────────────────────────────────────────


class TestTelegramTransport:
    def test_get_updates_request_shape(self):
        captured = []
        def handler(req):
            captured.append(req)
            return httpx.Response(200, json={"ok": True, "result": []})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxTelegramTransport(token="TOK", client=client)
        t.get_updates(offset=42, timeout=10.0)
        req = captured[0]
        assert req.method == "POST"
        assert "/botTOK/getUpdates" in str(req.url)
        body = json.loads(req.content)
        assert body == {"offset": 42, "timeout": 10}
        client.close()

    def test_get_updates_parses_result(self):
        def handler(req):
            return httpx.Response(200, json={
                "ok": True,
                "result": [{
                    "update_id": 1,
                    "message": {"text": "hi", "from": {"id": 9}, "chat": {"id": 99}},
                }],
            })
        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxTelegramTransport(token="TOK", client=client)
        out = t.get_updates(offset=0, timeout=5.0)
        assert len(out) == 1
        assert out[0]["update_id"] == 1
        client.close()

    def test_send_message_request_shape(self):
        captured = []
        def handler(req):
            captured.append(req)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxTelegramTransport(token="TOK", client=client)
        t.send_message(chat_id="123", text="hello")
        req = captured[0]
        assert "/botTOK/sendMessage" in str(req.url)
        assert json.loads(req.content) == {"chat_id": "123", "text": "hello"}
        client.close()

    def test_500_raises_channel_error(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(500, text="oops"),
        ))
        t = HttpxTelegramTransport(token="TOK", client=client)
        with pytest.raises(ChannelError, match="500"):
            t.get_updates(offset=0, timeout=1.0)
        client.close()

    def test_api_not_ok_raises(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"ok": False, "description": "bad"}),
        ))
        t = HttpxTelegramTransport(token="TOK", client=client)
        with pytest.raises(ChannelError, match="not ok"):
            t.send_message(chat_id="x", text="y")
        client.close()

    def test_empty_token_rejected(self):
        with pytest.raises(ChannelError):
            HttpxTelegramTransport(token="")


# ─── Discord ─────────────────────────────────────────────────────────────────


class TestDiscordTransport:
    def test_fetch_messages_polls_each_channel(self):
        captured: list = []

        def handler(req):
            captured.append(req)
            return httpx.Response(200, json=[
                {"id": "100", "guild_id": "G", "author": {"id": "U"}, "content": "hi"},
            ])

        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxDiscordTransport(
            token="BOT", channel_ids=["c1", "c2"], client=client,
        )
        out = t.fetch_messages()
        assert len(captured) == 2
        assert len(out) == 2
        assert all(m["author_id"] == "U" for m in out)
        client.close()

    def test_fetch_uses_after_cursor_after_first_call(self):
        params_seen: list = []

        def handler(req):
            params_seen.append(dict(req.url.params))
            return httpx.Response(200, json=[
                {"id": "200", "author": {"id": "U"}, "content": "x"},
            ])
        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxDiscordTransport(token="BOT", channel_ids=["c1"], client=client)
        t.fetch_messages()  # first call: no `after`
        t.fetch_messages()  # second call: must include `after=200`
        assert "after" not in params_seen[0]
        assert params_seen[1].get("after") == "200"
        client.close()

    def test_send_message_request_shape(self):
        captured = []
        def handler(req):
            captured.append(req)
            return httpx.Response(200, json={"id": "999"})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxDiscordTransport(token="BOT", channel_ids=["c1"], client=client)
        t.send_message(channel_id="c1", text="hello")
        req = captured[0]
        assert req.method == "POST"
        assert "/channels/c1/messages" in str(req.url)
        assert json.loads(req.content) == {"content": "hello"}
        assert req.headers.get("authorization") == "Bot BOT"
        client.close()

    def test_send_500_raises(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(500, text="x"),
        ))
        t = HttpxDiscordTransport(token="B", channel_ids=["c"], client=client)
        with pytest.raises(ChannelError, match="500"):
            t.send_message(channel_id="c", text="y")
        client.close()

    def test_empty_token_rejected(self):
        with pytest.raises(ChannelError):
            HttpxDiscordTransport(token="", channel_ids=["c"])

    def test_no_channels_rejected(self):
        with pytest.raises(ChannelError):
            HttpxDiscordTransport(token="B", channel_ids=[])


# ─── Slack ───────────────────────────────────────────────────────────────────


class TestSlackTransport:
    def test_fetch_events_request_shape(self):
        captured = []
        def handler(req):
            captured.append(req)
            return httpx.Response(200, json={
                "ok": True,
                "messages": [
                    {"type": "message", "user": "U", "text": "hi",
                     "ts": "1.0", "team": "T"},
                ],
            })
        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C1"], client=client)
        out = t.fetch_events()
        assert len(out) == 1
        assert out[0]["text"] == "hi"
        assert out[0]["channel"] == "C1"
        req = captured[0]
        assert "/conversations.history" in str(req.url)
        assert dict(req.url.params)["channel"] == "C1"
        assert req.headers.get("authorization") == "Bearer xoxb"
        client.close()

    def test_post_message_request_shape(self):
        captured = []
        def handler(req):
            captured.append(req)
            return httpx.Response(200, json={"ok": True, "ts": "1.0"})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C1"], client=client)
        t.post_message(channel="C1", text="hi")
        req = captured[0]
        assert "/chat.postMessage" in str(req.url)
        assert json.loads(req.content) == {"channel": "C1", "text": "hi"}
        client.close()

    def test_api_not_ok_raises(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"ok": False, "error": "channel_not_found"}),
        ))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        with pytest.raises(ChannelError, match="not ok"):
            t.post_message(channel="C", text="x")
        client.close()

    def test_oldest_cursor_advances(self):
        params_seen: list = []
        def handler(req):
            params_seen.append(dict(req.url.params))
            return httpx.Response(200, json={
                "ok": True,
                "messages": [{"type": "message", "user": "U", "text": "x", "ts": "9.0"}],
            })
        client = httpx.Client(transport=httpx.MockTransport(handler))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        t.fetch_events()
        t.fetch_events()
        assert "oldest" not in params_seen[0]
        assert params_seen[1].get("oldest") == "9.0"
        client.close()

    def test_empty_token_rejected(self):
        with pytest.raises(ChannelError):
            HttpxSlackTransport(bot_token="", channel_ids=["C"])
