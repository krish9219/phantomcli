"""Discord adapter.

Mock-friendly transport, same shape as Telegram. The operator points
:class:`DiscordAdapter` at a transport that wraps ``discord.py``'s
Client; tests pass a plain dict-shaped fake.

Trust cap: **2**. Discord is a remote channel; same reasoning as
Telegram.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from phantom.channels.adapter import ChannelAdapter
from phantom.channels.event import ChannelEvent
from phantom.channels.message import ChannelMessage
from phantom.errors import ChannelError

__all__ = ["DiscordAdapter", "DiscordTransport"]


@runtime_checkable
class DiscordTransport(Protocol):
    def fetch_messages(self) -> list[dict[str, Any]]: ...
    def send_message(self, *, channel_id: str, text: str) -> dict[str, Any]: ...


class DiscordAdapter(ChannelAdapter):
    name = "discord"

    _MAX_BODY = 2000  # Discord per-message cap.

    def __init__(self, *, token: str, transport: DiscordTransport) -> None:
        if not token:
            raise ChannelError("discord adapter requires a non-empty token")
        self._token = token
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
            raise ChannelError("discord adapter is not connected")
        for msg in self._transport.fetch_messages():
            self._inbox.append(
                ChannelEvent(
                    channel=self.name,
                    user_id=str(msg.get("author_id", "anon")),
                    text=msg.get("content", ""),
                    reply_to=str(msg.get("channel_id", "")),
                    received_at=datetime.now(timezone.utc),
                    metadata={
                        "guild_id": msg.get("guild_id"),
                        "message_id": msg.get("id"),
                    },
                )
            )

    def next_event(self) -> ChannelEvent | None:
        return self._inbox.popleft() if self._inbox else None

    def send(self, message: ChannelMessage) -> None:
        if not self._connected:
            raise ChannelError("discord adapter is not connected")
        if message.channel != self.name:
            raise ChannelError(
                f"discord adapter received message for channel {message.channel!r}"
            )
        body = message.text
        if len(body) > self._MAX_BODY:
            body = body[: self._MAX_BODY - 16] + "\n\n[…truncated]"
        try:
            self._transport.send_message(channel_id=message.reply_to, text=body)
        except Exception as exc:
            raise ChannelError(f"discord send failed: {exc}") from exc
        self.sent.append(message)
