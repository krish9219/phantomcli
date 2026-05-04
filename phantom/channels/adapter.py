"""ChannelAdapter ABC — the contract every channel implements.

Adapters are stateful: they connect to their channel's transport
(WebSocket, HTTP polling, etc.), translate inbound traffic into
:class:`ChannelEvent` objects, and accept :class:`ChannelMessage`
objects to send back.

Lifecycle
---------

::

    adapter = TelegramAdapter(token="...")
    adapter.connect()      # open transport, authenticate
    while adapter.healthy():
        event = adapter.next_event()
        ...
    adapter.send(message)
    adapter.close()        # close transport cleanly

The router (:mod:`phantom.channels.router`) drives this lifecycle on
behalf of the agent loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from phantom.channels.event import ChannelEvent
from phantom.channels.message import ChannelMessage

__all__ = ["ChannelAdapter"]


class ChannelAdapter(ABC):
    """Abstract base for channel adapters.

    Subclasses must override :attr:`name`, :meth:`connect`, :meth:`close`,
    :meth:`send`, :meth:`healthy`, and :meth:`max_trust_level`.
    Optionally :meth:`next_event` for inbound polling adapters.
    """

    #: Stable adapter identifier used in routing and config.
    name: ClassVar[str] = ""

    @abstractmethod
    def connect(self) -> None:
        """Open the channel's transport. Called once per session."""

    @abstractmethod
    def close(self) -> None:
        """Close the transport. Idempotent — calling twice is allowed."""

    @abstractmethod
    def send(self, message: ChannelMessage) -> None:
        """Deliver *message* to the channel.

        Raises :class:`phantom.errors.ChannelError` on transport failure;
        the router decides whether to retry or drop the message.
        """

    @abstractmethod
    def healthy(self) -> bool:
        """Return True iff the transport is connected and accepting traffic."""

    @abstractmethod
    def max_trust_level(self) -> int:
        """Return the highest trust level commands originating on this
        channel may use.

        ``4`` = God Mode allowed (only for the local CLI).
        ``3`` = developer-grade shell (default for desktop / private
        web channels).
        ``2`` = safe-prefix-only (the conservative default for chat
        channels like Telegram, Discord).
        ``1`` = paranoid; every command needs explicit OK.

        The router enforces this cap before dispatching to the
        executor.
        """

    # ─── inbound polling (optional) ────────────────────────────────────

    def next_event(self) -> ChannelEvent | None:  # pragma: no cover — opt-in
        """Pop the next inbound event. ``None`` if the queue is empty.

        Adapters that push events (WebSocket-driven) override this with
        a blocking implementation; HTTP-polling adapters override it to
        return immediately.
        """
        return None
