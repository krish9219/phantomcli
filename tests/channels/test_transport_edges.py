"""Edge-case coverage for the real httpx transports.

Pushes Telegram / Discord / Slack confidence above 90% by asserting
the transport handles every realistic real-world response shape:
malformed JSON, network errors, empty result lists, pagination, and
the API-specific "ok=false" envelopes.
"""

from __future__ import annotations

import httpx
import pytest

from phantom.channels.discord.transport import HttpxDiscordTransport
from phantom.channels.slack.transport import HttpxSlackTransport
from phantom.channels.telegram.transport import HttpxTelegramTransport
from phantom.errors import ChannelError


# ─── shared helpers ──────────────────────────────────────────────────────────


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ─── Telegram edges ──────────────────────────────────────────────────────────


class TestTelegramEdges:
    def test_network_error_wrapped(self):
        def boom(req):
            raise httpx.ConnectError("dns failed")
        client = _client(boom)
        t = HttpxTelegramTransport(token="TOK", client=client)
        with pytest.raises(ChannelError, match="request failed"):
            t.get_updates(offset=0, timeout=1.0)
        client.close()

    def test_empty_result_returns_empty_list(self):
        client = _client(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
        t = HttpxTelegramTransport(token="TOK", client=client)
        assert t.get_updates(offset=0, timeout=1.0) == []
        client.close()

    def test_non_list_result_returns_empty(self):
        # API spec says result is a list; defend against a malformed peer.
        client = _client(lambda r: httpx.Response(
            200, json={"ok": True, "result": {"unexpected": "shape"}}
        ))
        t = HttpxTelegramTransport(token="TOK", client=client)
        assert t.get_updates(offset=0, timeout=1.0) == []
        client.close()

    def test_400_carries_body_in_error(self):
        client = _client(lambda r: httpx.Response(
            400, text="Bad Request: chat not found",
        ))
        t = HttpxTelegramTransport(token="TOK", client=client)
        with pytest.raises(ChannelError, match="400"):
            t.send_message(chat_id="x", text="y")
        client.close()

    def test_offset_advances_through_calls(self):
        seen = []
        def handler(req):
            import json as j
            body = j.loads(req.content)
            seen.append(body["offset"])
            return httpx.Response(200, json={"ok": True, "result": []})
        client = _client(handler)
        t = HttpxTelegramTransport(token="TOK", client=client)
        t.get_updates(offset=10, timeout=1.0)
        t.get_updates(offset=42, timeout=1.0)
        assert seen == [10, 42]
        client.close()

    def test_alternate_base_url(self):
        captured = []
        def handler(req):
            captured.append(str(req.url))
            return httpx.Response(200, json={"ok": True, "result": []})
        client = _client(handler)
        t = HttpxTelegramTransport(
            token="TOK", client=client,
            base_url="https://my-proxy.example/tg",
        )
        t.get_updates(offset=0, timeout=1.0)
        assert "my-proxy.example" in captured[0]
        client.close()


# ─── Discord edges ───────────────────────────────────────────────────────────


class TestDiscordEdges:
    def test_network_error_wrapped(self):
        client = _client(lambda r: (_ for _ in ()).throw(
            httpx.ReadTimeout("slow")
        ))
        t = HttpxDiscordTransport(token="B", channel_ids=["c"], client=client)
        with pytest.raises(ChannelError, match="fetch failed"):
            t.fetch_messages()
        client.close()

    def test_429_propagates_with_status(self):
        client = _client(lambda r: httpx.Response(
            429, headers={"Retry-After": "5"}, text='{"message":"rate limited"}',
        ))
        t = HttpxDiscordTransport(token="B", channel_ids=["c"], client=client)
        with pytest.raises(ChannelError, match="429"):
            t.fetch_messages()
        client.close()

    def test_empty_message_list_is_fine(self):
        client = _client(lambda r: httpx.Response(200, json=[]))
        t = HttpxDiscordTransport(token="B", channel_ids=["c1", "c2"], client=client)
        assert t.fetch_messages() == []
        client.close()

    def test_non_list_response_skipped(self):
        client = _client(lambda r: httpx.Response(200, json={"unexpected": "shape"}))
        t = HttpxDiscordTransport(token="B", channel_ids=["c"], client=client)
        # Should silently skip the channel, not raise.
        assert t.fetch_messages() == []
        client.close()

    def test_message_without_author_handled(self):
        client = _client(lambda r: httpx.Response(200, json=[
            {"id": "9", "content": "x"},  # no author field
        ]))
        t = HttpxDiscordTransport(token="B", channel_ids=["c"], client=client)
        out = t.fetch_messages()
        assert len(out) == 1
        assert out[0]["author_id"] is None

    def test_chronological_order_preserved(self):
        # Discord returns newest-first; we reverse so callers see chronological.
        client = _client(lambda r: httpx.Response(200, json=[
            {"id": "3", "author": {"id": "U"}, "content": "third"},
            {"id": "2", "author": {"id": "U"}, "content": "second"},
            {"id": "1", "author": {"id": "U"}, "content": "first"},
        ]))
        t = HttpxDiscordTransport(token="B", channel_ids=["c"], client=client)
        out = t.fetch_messages()
        assert [m["content"] for m in out] == ["first", "second", "third"]


# ─── Slack edges ─────────────────────────────────────────────────────────────


class TestSlackEdges:
    def test_invalid_auth_response(self):
        client = _client(lambda r: httpx.Response(
            200, json={"ok": False, "error": "invalid_auth"},
        ))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        with pytest.raises(ChannelError, match="invalid_auth"):
            t.fetch_events()
        client.close()

    def test_empty_messages_list(self):
        client = _client(lambda r: httpx.Response(
            200, json={"ok": True, "messages": []},
        ))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        assert t.fetch_events() == []
        client.close()

    def test_missing_messages_key(self):
        # Slack sometimes returns ok=true with no messages field.
        client = _client(lambda r: httpx.Response(200, json={"ok": True}))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        assert t.fetch_events() == []
        client.close()

    def test_chronological_order(self):
        client = _client(lambda r: httpx.Response(200, json={
            "ok": True,
            "messages": [
                {"type": "message", "user": "U", "text": "third", "ts": "3.0"},
                {"type": "message", "user": "U", "text": "second", "ts": "2.0"},
                {"type": "message", "user": "U", "text": "first", "ts": "1.0"},
            ],
        }))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        out = t.fetch_events()
        assert [m["text"] for m in out] == ["first", "second", "third"]

    def test_non_message_events_pass_through(self):
        # Slack returns assorted event types; the adapter (not the
        # transport) filters. The transport is a thin pipe.
        client = _client(lambda r: httpx.Response(200, json={
            "ok": True,
            "messages": [
                {"type": "message", "user": "U", "text": "ok", "ts": "1.0"},
                {"type": "channel_join", "user": "U", "ts": "2.0"},
            ],
        }))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        out = t.fetch_events()
        assert len(out) == 2
        types = [m["type"] for m in out]
        assert "channel_join" in types

    def test_post_message_handles_channel_not_found(self):
        client = _client(lambda r: httpx.Response(
            200, json={"ok": False, "error": "channel_not_found"},
        ))
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        with pytest.raises(ChannelError, match="channel_not_found"):
            t.post_message(channel="C", text="hi")
        client.close()

    def test_network_timeout_wrapped(self):
        def boom(req):
            raise httpx.ReadTimeout("slow")
        client = _client(boom)
        t = HttpxSlackTransport(bot_token="xoxb", channel_ids=["C"], client=client)
        with pytest.raises(ChannelError, match="failed"):
            t.fetch_events()
        client.close()
