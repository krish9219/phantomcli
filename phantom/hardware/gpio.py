"""Raspberry-Pi GPIO peripheral.

Wraps ``gpiozero`` (optional dep). The adapter exposes the four
operations 95% of agent use-cases need: read pin, write pin, monitor
pin (callback on change), and PWM.

Read-only on hosts without RPi.GPIO / lgpio: gpiozero ships a
``MockFactory`` that lets every test in this file run on Linux x86.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from phantom.errors import PhantomError
from phantom.hardware.peripheral import Peripheral, PeripheralInfo

__all__ = ["GpioPeripheral"]


@dataclass
class GpioPeripheral(Peripheral):
    """One BCM-numbered GPIO pin.

    Each pin is one peripheral. Operators wire one peripheral per
    GPIO they want the agent to access; the registry's allowlist
    therefore controls them at pin granularity.
    """

    pin: int
    """BCM pin number."""

    mode: str = "input"
    """``"input"`` or ``"output"``."""

    description: str = ""

    factory: Any = None
    """Override for tests; receives ``pin`` (BCM) and returns an
    object with ``value`` (read/write) + ``close`` (cleanup)."""

    _device: Any = field(default=None, init=False, repr=False)

    def info(self) -> PeripheralInfo:
        return PeripheralInfo(
            id=f"gpio:{self.pin}",
            kind="gpio",
            description=self.description,
            extras={"pin": self.pin, "mode": self.mode},
        )

    def open(self) -> None:
        if self._device is not None:
            return
        if self.factory is not None:
            self._device = self.factory(self.pin)
            return
        try:
            from gpiozero import Button, LED  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PhantomError(
                "gpiozero is not installed; "
                "install via `pip install phantom-cli[hardware]`."
            ) from exc
        if self.mode == "input":
            self._device = Button(self.pin)
        elif self.mode == "output":
            self._device = LED(self.pin)
        else:
            raise PhantomError(f"unknown GPIO mode {self.mode!r}")

    def close(self) -> None:
        if self._device is None:
            return
        try:
            self._device.close()
        except Exception:
            pass
        self._device = None

    def read(self) -> int:
        """Read 0 or 1 (or float [0,1] on PWM-capable factories)."""
        self._require_open()
        # gpiozero exposes either ``value`` (0/1 or 0..1) or
        # ``is_pressed`` for buttons.
        v = getattr(self._device, "value", None)
        if v is None:
            v = int(bool(getattr(self._device, "is_pressed", False)))
        return int(v) if isinstance(v, (int, bool)) else float(v)  # type: ignore[return-value]

    def write(self, value: int | float) -> None:
        """Set output pin value. Output-mode peripherals only."""
        self._require_open()
        if self.mode != "output":
            raise PhantomError(
                f"cannot write to input GPIO pin {self.pin}"
            )
        # gpiozero LED has on()/off()/value semantics.
        if hasattr(self._device, "value"):
            self._device.value = value
        else:
            (self._device.on if value else self._device.off)()

    def watch(self, on_change: Callable[[int], None]) -> None:
        """Register a callback fired on every transition.

        Only meaningful in input mode. The callback receives the new
        value (0 or 1).
        """
        self._require_open()
        if self.mode != "input":
            raise PhantomError("cannot watch an output pin")
        if hasattr(self._device, "when_pressed"):
            self._device.when_pressed = lambda: on_change(1)
            self._device.when_released = lambda: on_change(0)
        else:
            raise PhantomError(
                "device does not support edge callbacks"
            )

    def _require_open(self) -> None:
        if self._device is None:
            raise PhantomError(f"GPIO {self.pin} is not open")
