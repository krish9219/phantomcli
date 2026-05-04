"""Telegram adapter.

Wraps the Telegram Bot API. Stage 3 ships a mock-friendly transport so
the adapter is fully unit-testable without hitting the live API; the
operator supplies a real ``TelegramTransport`` (HTTP polling against
api.telegram.org) at runtime.

Trust cap: **2** (safe-prefix-only). Telegram is a remote channel and
the user's phone may be lost or compromised; God Mode is **never**
allowed to originate from Telegram. This is enforced by
:meth:`max_trust_level` and double-checked by the executor's blocklist.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from phantom.channels.adapter import ChannelAdapter
from phantom.channels.event import ChannelEvent
from phantom.channels.message import ChannelMessage
from phantom.errors import ChannelError

__all__ = ["TelegramAdapter", "TelegramTransport"]


@runtime_checkable
class TelegramTransport(Protocol):
    """Minimum surface a transport must offer.

    The real transport is an HTTP client speaking the Bot API. Tests
    pass a plain object with the same methods.
    """

    def get_updates(self, *, offset: int, timeout: float) -> list[dict[str, Any]]: ...
    def send_message(self, *, chat_id: str, text: str) -> dict[str, Any]: ...


class TelegramAdapter(ChannelAdapter):
    """Telegram Bot API adapter."""

    name = "telegram"

    def __init__(
        self,
        *,
        token: str,
        transport: TelegramTransport,
        long_poll_timeout_s: float = 25.0,
    ) -> None:
        if not token:
            raise ChannelError("telegram adapter requires a non-empty token")
        self._token = token
        self._transport = transport
        self._timeout = long_poll_timeout_s
        self._connected = False
        self._offset = 0
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
        # Hard policy: Telegram never escalates above level 2.
        return 2

    # ─── poll for inbound updates ──────────────────────────────────────

    def poll(self) -> None:
        """One long-poll cycle. The router calls this in a loop."""
        if not self._connected:
            raise ChannelError("telegram adapter is not connected")
        updates = self._transport.get_updates(offset=self._offset, timeout=self._timeout)
        for upd in updates:
            update_id = int(upd.get("update_id", 0))
            self._offset = max(self._offset, update_id + 1)
            msg = upd.get("message")
            if not msg:
                continue
            chat = msg.get("chat") or {}
            user = msg.get("from") or {}
            text = msg.get("text", "")
            self._inbox.append(
                ChannelEvent(
                    channel=self.name,
                    user_id=str(user.get("id", "anon")),
                    text=text,
                    reply_to=str(chat.get("id", "")),
                    received_at=datetime.now(timezone.utc),
                    metadata={"telegram_update_id": update_id},
                )
            )

    def next_event(self) -> ChannelEvent | None:
        return self._inbox.popleft() if self._inbox else None

    # ─── outbound ──────────────────────────────────────────────────────

    _MAX_BODY = 4096  # Telegram limit.

    def send(self, message: ChannelMessage) -> None:
        if not self._connected:
            raise ChannelError("telegram adapter is not connected")
        if message.channel != self.name:
            raise ChannelError(
                f"telegram adapter received message for channel {message.channel!r}"
            )
        body = message.text
        if len(body) > self._MAX_BODY:
            body = body[: self._MAX_BODY - 16] + "\n\n[…truncated]"
        try:
            self._transport.send_message(chat_id=message.reply_to, text=body)
        except Exception as exc:
            raise ChannelError(f"telegram send failed: {exc}") from exc
        self.sent.append(message)
