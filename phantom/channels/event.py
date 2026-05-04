"""Inbound channel events — what the agent loop receives.

A :class:`ChannelEvent` is the channel-agnostic representation of "the
user said something". Each adapter translates its native protocol's
"new message" event into this shape; the agent loop never sees Telegram
chat IDs or Discord guild IDs directly.

Privacy note: the event carries the raw text but **does not** carry
attachments or media payloads. Media is referenced by URL or by a
sandbox-mounted file path; the agent loop fetches it on demand
through a dedicated tool. This keeps event objects small and
log-friendly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = ["ChannelEvent"]


@dataclass(frozen=True, slots=True)
class ChannelEvent:
    """One inbound message from a channel.

    Attributes
    ----------
    channel:
        Adapter name (``"telegram"`` / ``"discord"`` / …). Stable across
        releases; the router uses it to look up the response adapter.
    user_id:
        Channel-native user identifier as a string. Phantom does not
        normalise these; two users with the same display name on
        different channels remain distinct identities.
    text:
        Message body, UTF-8.
    received_at:
        UTC datetime stamped by the adapter when the event was received
        on its side. Used for rate-limiting and deduplication.
    reply_to:
        Channel-native conversation handle the response should target
        (e.g. Telegram chat_id). Adapters preserve this opaquely so the
        agent loop never has to construct one.
    metadata:
        Free-form dict for adapter-specific extras (workspace IDs,
        thread IDs, etc.). Consumers must defensively read with
        ``metadata.get(...)``; keys are not standardised.
    """

    channel: str
    user_id: str
    text: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reply_to: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None:
            raise ValueError("ChannelEvent.received_at must be timezone-aware")
