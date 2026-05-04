"""ChannelRouter — agent-loop-facing dispatcher.

Holds a registry of named adapters, accepts :class:`ChannelMessage`
objects, and routes each to the matching adapter. Enforces trust caps
on inbound :class:`ChannelEvent` objects: the router clamps the
caller's requested trust level to the adapter's
:meth:`ChannelAdapter.max_trust_level`.
"""

from __future__ import annotations

from dataclasses import dataclass

from phantom.channels.adapter import ChannelAdapter
from phantom.channels.event import ChannelEvent
from phantom.channels.message import ChannelMessage
from phantom.errors import ChannelError

__all__ = ["ChannelRouter"]


@dataclass
class ChannelRouter:
    """Maintain a registry of channel adapters and enforce trust caps."""

    _adapters: dict[str, ChannelAdapter]

    def __init__(self) -> None:
        self._adapters = {}

    # ─── registry ──────────────────────────────────────────────────────

    def register(self, adapter: ChannelAdapter) -> None:
        """Register *adapter* under its :attr:`name`. Replaces any prior
        registration with the same name."""
        if not adapter.name:
            raise ChannelError("adapter has no name")
        self._adapters[adapter.name] = adapter

    def unregister(self, name: str) -> None:
        """Remove the adapter named *name*. Idempotent."""
        self._adapters.pop(name, None)

    def names(self) -> list[str]:
        """Sorted list of registered adapter names."""
        return sorted(self._adapters)

    def get(self, name: str) -> ChannelAdapter:
        """Return the adapter named *name*. Raises if not registered."""
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise ChannelError(f"no adapter registered for channel {name!r}") from exc

    # ─── dispatch ──────────────────────────────────────────────────────

    def send(self, message: ChannelMessage) -> None:
        """Route *message* to the adapter named ``message.channel``."""
        self.get(message.channel).send(message)

    def trust_for(self, event: ChannelEvent, requested: int) -> int:
        """Clamp *requested* trust level to the adapter's cap.

        Returns the *effective* trust level the executor should run
        under. If the adapter is not registered (rare; an event from
        an unknown channel), trust falls back to 1 (paranoid).
        """
        if event.channel not in self._adapters:
            return 1
        cap = self._adapters[event.channel].max_trust_level()
        return min(requested, cap)
