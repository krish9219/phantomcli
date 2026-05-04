"""WebChat adapter — embedded WebSocket chat in the dashboard.

Owns no external service. Inbound traffic arrives via the dashboard's
WebSocket route; outbound traffic flows back over the same socket. The
adapter is therefore extremely simple: it's a queue + a callback.

Trust cap: 3 (developer). The dashboard binds to loopback by default,
so WebChat is "the local user" — same trust as the CLI minus God Mode.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Callable

from phantom.channels.adapter import ChannelAdapter
from phantom.channels.event import ChannelEvent
from phantom.channels.message import ChannelMessage
from phantom.errors import ChannelError

__all__ = ["WebChatAdapter"]


class WebChatAdapter(ChannelAdapter):
    """Dashboard-embedded chat. Stateless; the dashboard owns the socket."""

    name = "webchat"

    def __init__(
        self,
        *,
        outbound: Callable[[ChannelMessage], None] | None = None,
    ) -> None:
        # The dashboard binds an ``outbound`` callback at startup; tests
        # leave it None and inspect ``self.sent``.
        self._outbound = outbound
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
        return 3

    # ─── inbound side (dashboard pushes events here) ───────────────────

    def receive(self, *, user_id: str, text: str, reply_to: str = "") -> None:
        """Push a synthetic event into the inbox.

        Used by the dashboard's WebSocket handler when a user types a
        line. Tests call it directly to seed the inbox.
        """
        if not self._connected:
            raise ChannelError("webchat adapter is not connected")
        self._inbox.append(
            ChannelEvent(
                channel=self.name,
                user_id=user_id,
                text=text,
                reply_to=reply_to or user_id,
                received_at=datetime.now(timezone.utc),
            )
        )

    def next_event(self) -> ChannelEvent | None:
        if not self._inbox:
            return None
        return self._inbox.popleft()

    # ─── outbound ──────────────────────────────────────────────────────

    def send(self, message: ChannelMessage) -> None:
        if not self._connected:
            raise ChannelError("webchat adapter is not connected")
        if message.channel != self.name:
            raise ChannelError(
                f"webchat adapter received message for channel {message.channel!r}"
            )
        self.sent.append(message)
        if self._outbound is not None:
            self._outbound(message)
