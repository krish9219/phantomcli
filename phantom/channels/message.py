"""Outbound channel messages — what the agent loop sends.

A :class:`ChannelMessage` is what the agent decides to send back. The
adapter is responsible for translating it into a channel-native send
operation; the agent never constructs a Telegram-shaped payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ChannelMessage"]


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    """One outbound message to a channel.

    Attributes
    ----------
    channel:
        Target adapter. Must match a registered adapter, otherwise the
        router raises :class:`phantom.errors.ChannelError`.
    reply_to:
        Channel-native conversation handle. Usually copied from the
        inbound :class:`ChannelEvent` that triggered this response.
    text:
        Body. UTF-8. Adapters may chunk or truncate to fit the
        channel's per-message limit (Slack: 4 000 chars; Discord:
        2 000; Telegram: 4 096); chunking is announced via a
        continuation marker.
    parse_mode:
        ``"plain"`` (default), ``"markdown"``, or ``"html"``. Adapters
        translate to the channel's native syntax; unsupported modes
        fall back to plain.
    extras:
        Adapter-specific bag (e.g. ``{"reply_markup": …}`` for
        Telegram). Keys are not standardised; document expected keys
        in the adapter's own README.
    """

    channel: str
    reply_to: str
    text: str
    parse_mode: str = "plain"
    extras: dict[str, Any] = field(default_factory=dict)
