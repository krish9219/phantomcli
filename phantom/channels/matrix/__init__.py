"""Matrix adapter.

Speaks the Client-Server API r0.6 / v1.x. Polls ``/sync`` for events
and POSTs to ``/rooms/{roomId}/send/{eventType}/{txnId}`` for outbound.

Trust cap: **2**. Matrix is a remote channel; safe-prefix-only by
default. Operators who pair Matrix with end-to-end-encryption can
override per-room via the routing layer.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from phantom.channels.adapter import ChannelAdapter
from phantom.channels.event import ChannelEvent
from phantom.channels.message import ChannelMessage
from phantom.errors import ChannelError

__all__ = ["MatrixAdapter", "MatrixTransport"]


@runtime_checkable
class MatrixTransport(Protocol):
    """Mock-friendly transport contract.

    Real backend is :class:`HttpxMatrixTransport`; tests pass a fake
    that returns canned event lists.
    """

    def fetch_events(self) -> list[dict[str, Any]]: ...

    def send_message(self, *, room_id: str, body: str) -> dict[str, Any]: ...


class MatrixAdapter(ChannelAdapter):
    """Matrix Client-Server adapter."""

    name = "matrix"

    def __init__(
        self,
        *,
        transport: MatrixTransport,
        user_id: str = "",
        max_buffered_events: int = 256,
    ) -> None:
        if transport is None:
            raise ChannelError("MatrixAdapter requires a transport")
        self._transport = transport
        self._user_id = user_id
        self._connected = False
        self._buffer: deque[ChannelEvent] = deque(maxlen=max_buffered_events)

    # ── lifecycle ────────────────────────────────────────────────────

    def connect(self) -> None:
        # We don't pre-validate against the homeserver — first
        # fetch_events surfaces transport errors via ChannelError.
        self._connected = True

    def close(self) -> None:
        self._connected = False
        # Closing twice is a no-op per the ABC contract.

    def healthy(self) -> bool:
        return self._connected

    def max_trust_level(self) -> int:
        return 2

    # ── inbound ──────────────────────────────────────────────────────

    def next_event(self) -> ChannelEvent | None:
        if not self._connected:
            return None
        if not self._buffer:
            self._refill()
        return self._buffer.popleft() if self._buffer else None

    def _refill(self) -> None:
        try:
            raw = self._transport.fetch_events()
        except ChannelError:
            raise
        except Exception as exc:
            raise ChannelError(f"matrix fetch failed: {exc}") from exc
        for ev in raw:
            # Skip our own messages — many Matrix clients echo them back.
            sender = ev.get("sender", "")
            if self._user_id and sender == self._user_id:
                continue
            content = ev.get("content") or {}
            msgtype = content.get("msgtype", "m.text")
            # Only handle text-style events; ignore m.image / m.file.
            if msgtype not in ("m.text", "m.notice"):
                continue
            text = content.get("body", "")
            if not isinstance(text, str) or not text:
                continue
            ts = ev.get("origin_server_ts")
            if isinstance(ts, (int, float)):
                ts_dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
            else:
                ts_dt = datetime.now(tz=timezone.utc)
            self._buffer.append(ChannelEvent(
                channel=self.name,
                user_id=str(sender),
                text=text,
                received_at=ts_dt,
                reply_to=str(ev.get("event_id", "")),
                metadata={
                    "room_id": str(ev.get("room_id", "")),
                    "msgtype": msgtype,
                    "raw": ev,
                },
            ))

    # ── outbound ─────────────────────────────────────────────────────

    def send(self, message: ChannelMessage) -> None:
        if not self._connected:
            raise ChannelError("matrix adapter not connected")
        # The router puts the destination room in `extras["room_id"]` (or
        # falls back to `reply_to` for direct replies).
        room_id = str(message.extras.get("room_id") or message.reply_to or "")
        if not room_id:
            raise ChannelError("matrix outbound message requires extras.room_id or reply_to")
        try:
            self._transport.send_message(room_id=room_id, body=message.text)
        except ChannelError:
            raise
        except Exception as exc:
            raise ChannelError(f"matrix send failed: {exc}") from exc
