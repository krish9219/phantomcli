"""Phantom multi-channel framework.

ADR-0003 + ADR-0006 establish the contract: channels are dumb
translators of agent events ↔ channel-native protocol. All policy
(trust caps, command gating, rate limiting) lives in the routing
layer.

Public surface:

* :class:`ChannelAdapter` — the ABC every channel implements.
* :class:`ChannelEvent`   — inbound message shape.
* :class:`ChannelMessage` — outbound message shape.
* :class:`ChannelRouter`  — registry + dispatcher, enforces trust caps.

Stage 3 ships four adapters: WebChat, Telegram, Discord, Slack. Matrix
and IRC adapters are deferred to Stage 8 (they need real homeservers
to verify end-to-end).
"""

from __future__ import annotations

from phantom.channels.adapter import ChannelAdapter
from phantom.channels.event import ChannelEvent
from phantom.channels.message import ChannelMessage
from phantom.channels.router import ChannelRouter

__all__ = [
    "ChannelAdapter",
    "ChannelEvent",
    "ChannelMessage",
    "ChannelRouter",
]
