"""Serial-port peripheral adapter.

Wraps ``pyserial`` (optional dep, ``pip install phantom-cli[hardware]``)
to read/write a UART. Used for: USB-to-serial breakouts, ESP32 / Arduino
console, modems, GPS receivers.

The adapter is **synchronous and exclusive** — one process at a time
holds the port. The registry decides who gets in.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from phantom.errors import PhantomError
from phantom.hardware.peripheral import Peripheral, PeripheralInfo

__all__ = ["SerialPeripheral", "list_serial_ports"]


def list_serial_ports() -> list[PeripheralInfo]:
    """Discover serial ports on the host. Returns an empty list when
    pyserial is not installed (no exception)."""
    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]
    except ImportError:
        return []
    out: list[PeripheralInfo] = []
    for port in list_ports.comports():
        out.append(PeripheralInfo(
            id=f"serial:{port.device}",
            kind="serial",
            description=port.description or "",
            extras={
                "device": port.device,
                "vid": port.vid, "pid": port.pid,
                "manufacturer": port.manufacturer,
                "serial_number": port.serial_number,
            },
        ))
    return out


@dataclass
class SerialPeripheral(Peripheral):
    """Serial UART peripheral.

    Construction does not open the port. Call :meth:`open` first.
    """

    device: str
    """Path: e.g. ``/dev/ttyUSB0`` or ``COM3``."""

    baudrate: int = 115200

    timeout_s: float = 1.0
    """Default read timeout. Per-call timeouts override."""

    description: str = ""

    serial_factory: Any = None
    """Override for tests; receives kwargs and returns a Serial-like
    object with ``read``/``write``/``close``/``flush`` methods."""

    _conn: Any = field(default=None, init=False, repr=False)

    def info(self) -> PeripheralInfo:
        return PeripheralInfo(
            id=f"serial:{self.device}",
            kind="serial",
            description=self.description,
            extras={"device": self.device, "baudrate": self.baudrate},
        )

    def open(self) -> None:
        if self._conn is not None:
            return
        if self.serial_factory is not None:
            self._conn = self.serial_factory(
                port=self.device, baudrate=self.baudrate, timeout=self.timeout_s,
            )
            return
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PhantomError(
                "pyserial is not installed; "
                "install via `pip install phantom-cli[hardware]`."
            ) from exc
        try:
            self._conn = serial.Serial(
                port=self.device,
                baudrate=self.baudrate,
                timeout=self.timeout_s,
            )
        except Exception as exc:
            raise PhantomError(
                f"could not open serial port {self.device!r}: {exc}"
            ) from exc

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = None

    def read_bytes(self, n: int = 4096, *, timeout_s: float = 1.0) -> bytes:
        self._require_open()
        # pyserial's per-call timeout: re-set on the underlying object.
        try:
            self._conn.timeout = timeout_s
        except Exception:
            pass
        data = self._conn.read(n)
        if not isinstance(data, (bytes, bytearray)):
            raise PhantomError(
                f"serial read returned {type(data).__name__}, expected bytes"
            )
        return bytes(data)

    def write_bytes(self, data: bytes) -> int:
        self._require_open()
        n = self._conn.write(data)
        # pyserial's flush ensures data leaves the kernel buffer.
        try:
            self._conn.flush()
        except Exception:
            pass
        return int(n) if n is not None else len(data)

    def write_line(self, line: str, *, eol: str = "\n") -> int:
        return self.write_bytes((line + eol).encode("utf-8"))

    def read_line(self, *, timeout_s: float = 1.0) -> str:
        """Read one line terminated by ``\\n``. Empty string on timeout."""
        deadline = time.monotonic() + timeout_s
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self.read_bytes(1, timeout_s=0.1)
            if not chunk:
                continue
            buf.extend(chunk)
            if chunk == b"\n":
                break
        return buf.decode("utf-8", errors="replace")

    def _require_open(self) -> None:
        if self._conn is None:
            raise PhantomError(f"serial port {self.device!r} is not open")
