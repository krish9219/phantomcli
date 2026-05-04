"""Peripheral ABC and registry.

A peripheral is any external device the agent can read from or write
to. Adapters share a small surface — open / close / read / write /
metadata — and the registry enforces operator allow/deny policy on
top.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from phantom.errors import PermissionDeniedError, PhantomError

__all__ = [
    "Peripheral",
    "PeripheralInfo",
    "PeripheralRegistry",
]


@dataclass(frozen=True, slots=True)
class PeripheralInfo:
    """Metadata about a discovered peripheral."""

    id: str
    """Stable identifier — ``"serial:/dev/ttyUSB0"``, ``"mqtt:tcp://broker:1883"``."""

    kind: str
    """``"serial"`` / ``"usb"`` / ``"mqtt"`` / ``"gpio"`` / future."""

    description: str = ""

    extras: dict[str, Any] = field(default_factory=dict)


class Peripheral(ABC):
    """Abstract base for hardware adapters.

    Subclasses must implement :meth:`info`, :meth:`open`, :meth:`close`,
    :meth:`read_bytes`, and :meth:`write_bytes`. Adapters that don't
    naturally fit a byte-stream model (MQTT, GPIO) override the
    ``read``/``write`` methods at a higher abstraction.
    """

    @abstractmethod
    def info(self) -> PeripheralInfo: ...

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def read_bytes(self, n: int = 4096, *, timeout_s: float = 1.0) -> bytes:
        """Read up to *n* bytes. Default: not supported (raise)."""
        raise PhantomError(
            f"{type(self).__name__}.read_bytes is not supported"
        )

    def write_bytes(self, data: bytes) -> int:
        """Write *data*; return bytes written. Default: not supported (raise)."""
        raise PhantomError(
            f"{type(self).__name__}.write_bytes is not supported"
        )


# ─── registry ────────────────────────────────────────────────────────────────


@dataclass
class PeripheralRegistry:
    """Operator-gated registry of available peripherals.

    Two policy lists govern access:

    * ``allowlist`` — if non-empty, only IDs in this set are usable.
    * ``denylist`` — IDs in this set are blocked even if the
      allowlist is empty.

    The registry never opens devices itself; consumers call
    :meth:`acquire` to get a peripheral they then ``open()`` /
    ``close()``. The registry's job is solely access control + a
    single source of truth for "what's plugged in".
    """

    allowlist: frozenset[str] = field(default_factory=frozenset)
    denylist: frozenset[str] = field(default_factory=frozenset)
    _peripherals: dict[str, Peripheral] = field(default_factory=dict)

    def register(self, p: Peripheral) -> None:
        info = p.info()
        if not info.id:
            raise PhantomError("peripheral has no id")
        self._peripherals[info.id] = p

    def unregister(self, peripheral_id: str) -> None:
        self._peripherals.pop(peripheral_id, None)

    def list(self) -> list[PeripheralInfo]:
        return [p.info() for p in self._peripherals.values()]

    def acquire(self, peripheral_id: str) -> Peripheral:
        """Return the peripheral *after* policy check.

        Raises :class:`phantom.errors.PermissionDeniedError` if the
        operator policy forbids it.
        """
        if peripheral_id not in self._peripherals:
            raise PhantomError(
                f"unknown peripheral {peripheral_id!r}; "
                f"known: {sorted(self._peripherals)}"
            )
        if peripheral_id in self.denylist:
            raise PermissionDeniedError(
                f"peripheral {peripheral_id!r} is on the deny list"
            )
        if self.allowlist and peripheral_id not in self.allowlist:
            raise PermissionDeniedError(
                f"peripheral {peripheral_id!r} is not in the allow list"
            )
        return self._peripherals[peripheral_id]
