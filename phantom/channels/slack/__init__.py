"""Slack adapter.

Mock-friendly transport. Operator points it at a transport wrapping
``slack-sdk``'s WebClient + Socket Mode for inbound; tests pass a fake.

Trust cap: **2**. Slack is a remote channel.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from phantom.channels.adapter import ChannelAdapter
from phantom.channels.event import ChannelEvent
from phantom.channels.message import ChannelMessage
from phantom.errors import ChannelError

__all__ = ["SlackAdapter", "SlackTransport"]


@runtime_checkable
class SlackTransport(Protocol):
    def fetch_events(self) -> list[dict[str, Any]]: ...
    def post_message(self, *, channel: str, text: str) -> dict[str, Any]: ...


class SlackAdapter(ChannelAdapter):
    name = "slack"

    _MAX_BODY = 4000  # Slack per-message practical limit.

    def __init__(self, *, bot_token: str, transport: SlackTransport) -> None:
        if not bot_token:
            raise ChannelError("slack adapter requires a non-empty bot_token")
        self._token = bot_token
        self._transport = transport
        self._connected = False
        self._inbox: deque[ChannelEvent] = deque()
        self.sent: list[ChannelMessage] = []

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False
        self._inbox.clear()

    def healthy(self) -> bool:
        return self._connected

    def max_trust_level(self) -> int:
        return 2

    def poll(self) -> None:
        if not self._connected:
            raise ChannelError("slack adapter is not connected")
        for evt in self._transport.fetch_events():
            etype = evt.get("type")
            if etype != "message":
                continue
            self._inbox.append(
                ChannelEvent(
                    channel=self.name,
                    user_id=str(evt.get("user", "anon")),
                    text=evt.get("text", ""),
                    reply_to=str(evt.get("channel", "")),
                    received_at=datetime.now(timezone.utc),
                    metadata={
                        "team": evt.get("team"),
                        "ts": evt.get("ts"),
                        "thread_ts": evt.get("thread_ts"),
                    },
                )
            )

    def next_event(self) -> ChannelEvent | None:
        return self._inbox.popleft() if self._inbox else None

    def send(self, message: ChannelMessage) -> None:
        if not self._connected:
            raise ChannelError("slack adapter is not connected")
        if message.channel != self.name:
            raise ChannelError(
                f"slack adapter received message for channel {message.channel!r}"
            )
        body = message.text
        if len(body) > self._MAX_BODY:
            body = body[: self._MAX_BODY - 16] + "\n\n[…truncated]"
        try:
            self._transport.post_message(channel=message.reply_to, text=body)
        except Exception as exc:
            raise ChannelError(f"slack send failed: {exc}") from exc
        self.sent.append(message)
