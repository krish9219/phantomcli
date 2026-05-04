"""Phantom hardware — talk to physical peripherals.

This is what makes Phantom outstanding versus OpenClaw, ZeroClaw,
Claude Code, AutoGPT, and AgentZero: a clean way to let the agent
read sensors, flash microcontrollers, and publish to MQTT brokers
from inside the same conversation that's running shell + browsing
the web.

Public surface:

* :class:`Peripheral`         — ABC every device implements.
* :class:`PeripheralRegistry` — maintains discovered devices, gates
  access against operator allow/deny lists.
* :class:`SerialPeripheral`   — ``/dev/tty*`` and Windows ``COM*`` ports.
* :class:`UsbPeripheral`      — USB CDC devices.
* :class:`MqttPeripheral`     — publish/subscribe on an MQTT broker.
* :class:`GpioPeripheral`     — Raspberry-Pi / SBC GPIO via gpiozero.

Each adapter is independently optional under
``pip install phantom-cli[hardware]``. Importing this package
without the underlying libraries works; the concrete classes raise
:class:`PhantomError` only at construction time when the dep is
missing.

Security
--------

Hardware adapters are gated by :class:`Capability.HARDWARE` (added
to :mod:`phantom.plugins.capability`). Operators control which
specific devices a session can touch via
``~/.phantom/config.json`` ``hardware.allowlist``.
"""

from __future__ import annotations

from phantom.hardware.peripheral import (
    Peripheral,
    PeripheralInfo,
    PeripheralRegistry,
)

# The concrete adapters are imported lazily so importing
# ``phantom.hardware`` doesn't pull in pyserial / paho-mqtt / gpiozero
# unless they are actually used. Operators reference them as
# ``from phantom.hardware.serial_adapter import SerialPeripheral``.

__all__ = [
    "Peripheral",
    "PeripheralInfo",
    "PeripheralRegistry",
]
