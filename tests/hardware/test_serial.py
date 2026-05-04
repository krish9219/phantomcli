"""Tests for :mod:`phantom.hardware.serial_adapter`.

Uses a fake Serial-like factory so tests run without pyserial / a real
UART. Verifies the adapter's lifecycle, read/write, line operations,
timeouts.
"""

from __future__ import annotations

from collections import deque

import pytest

from phantom.errors import PhantomError
from phantom.hardware.serial_adapter import (
    SerialPeripheral,
    list_serial_ports,
)


class _FakeSerial:
    """Minimal Serial-like double matching pyserial's API."""

    def __init__(self, *, port, baudrate, timeout):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.read_queue: deque[bytes] = deque()
        self.written: list[bytes] = []
        self.closed = False
        self.flushed = False

    def read(self, n):
        if not self.read_queue:
            return b""
        chunk = self.read_queue.popleft()
        return chunk[:n]

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def flush(self):
        self.flushed = True

    def close(self):
        self.closed = True


def _factory(initial_reads: list[bytes] | None = None):
    holder: dict = {}
    def make(*, port, baudrate, timeout):
        s = _FakeSerial(port=port, baudrate=baudrate, timeout=timeout)
        for r in (initial_reads or []):
            s.read_queue.append(r)
        holder["last"] = s
        return s
    make.holder = holder  # type: ignore[attr-defined]
    return make


# ─── basic lifecycle ────────────────────────────────────────────────────────


class TestLifecycle:
    def test_open_close(self):
        f = _factory()
        p = SerialPeripheral(device="/dev/ttyUSB0", serial_factory=f)
        p.open()
        assert f.holder["last"].port == "/dev/ttyUSB0"  # type: ignore[attr-defined]
        p.close()
        assert f.holder["last"].closed is True  # type: ignore[attr-defined]

    def test_double_open_idempotent(self):
        f = _factory()
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=f)
        p.open()
        p.open()  # must not raise nor reconnect
        p.close()

    def test_close_when_unopened_is_noop(self):
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=_factory())
        p.close()  # must not raise

    def test_info_round_trip(self):
        p = SerialPeripheral(device="/dev/ttyX", baudrate=9600,
                             description="esp32")
        info = p.info()
        assert info.id == "serial:/dev/ttyX"
        assert info.kind == "serial"
        assert info.extras["device"] == "/dev/ttyX"
        assert info.extras["baudrate"] == 9600


# ─── I/O ─────────────────────────────────────────────────────────────────────


class TestIO:
    def test_read_bytes_returns_data(self):
        f = _factory(initial_reads=[b"hello"])
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=f)
        p.open()
        assert p.read_bytes(10) == b"hello"

    def test_read_bytes_empty_when_buffer_empty(self):
        f = _factory()
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=f)
        p.open()
        assert p.read_bytes(10) == b""

    def test_write_bytes_flushes(self):
        f = _factory()
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=f)
        p.open()
        n = p.write_bytes(b"hi")
        assert n == 2
        assert f.holder["last"].written == [b"hi"]  # type: ignore[attr-defined]
        assert f.holder["last"].flushed is True  # type: ignore[attr-defined]

    def test_write_line_appends_eol(self):
        f = _factory()
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=f)
        p.open()
        p.write_line("AT")
        assert f.holder["last"].written == [b"AT\n"]  # type: ignore[attr-defined]

    def test_read_line_collects_bytes_until_lf(self):
        f = _factory(initial_reads=[b"O", b"K", b"\n", b"after"])
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=f)
        p.open()
        line = p.read_line(timeout_s=2.0)
        assert line == "OK\n"

    def test_read_bytes_when_unopened_raises(self):
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=_factory())
        with pytest.raises(PhantomError, match="not open"):
            p.read_bytes()

    def test_write_bytes_when_unopened_raises(self):
        p = SerialPeripheral(device="/dev/ttyX", serial_factory=_factory())
        with pytest.raises(PhantomError, match="not open"):
            p.write_bytes(b"x")


# ─── discovery ───────────────────────────────────────────────────────────────


class TestDiscovery:
    def test_list_serial_ports_returns_list(self):
        # Whether or not pyserial is installed, the function must
        # return a list (possibly empty) rather than raising.
        out = list_serial_ports()
        assert isinstance(out, list)
        for info in out:
            assert info.kind == "serial"
