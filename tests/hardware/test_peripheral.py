"""Tests for the Peripheral ABC + registry."""

from __future__ import annotations

import pytest

from phantom.errors import PermissionDeniedError, PhantomError
from phantom.hardware import (
    Peripheral,
    PeripheralInfo,
    PeripheralRegistry,
)
from phantom.plugins.capability import Capability


class _Dummy(Peripheral):
    def __init__(self, ident="x:0"):
        self.ident = ident
        self.opened = False

    def info(self):
        return PeripheralInfo(id=self.ident, kind="x", description="d")

    def open(self): self.opened = True
    def close(self): self.opened = False


class TestRegistry:
    def test_register_list(self):
        r = PeripheralRegistry()
        r.register(_Dummy("x:0"))
        r.register(_Dummy("x:1"))
        ids = {info.id for info in r.list()}
        assert ids == {"x:0", "x:1"}

    def test_register_no_id_rejected(self):
        class _NoId(_Dummy):
            def info(self):
                return PeripheralInfo(id="", kind="x")
        r = PeripheralRegistry()
        with pytest.raises(PhantomError, match="no id"):
            r.register(_NoId())

    def test_unregister(self):
        r = PeripheralRegistry()
        r.register(_Dummy("x:0"))
        r.unregister("x:0")
        assert r.list() == []
        # Idempotent.
        r.unregister("never-was-there")

    def test_acquire_unknown_raises(self):
        r = PeripheralRegistry()
        with pytest.raises(PhantomError, match="unknown peripheral"):
            r.acquire("x:99")

    def test_denylist_blocks(self):
        r = PeripheralRegistry(denylist=frozenset({"x:0"}))
        r.register(_Dummy("x:0"))
        r.register(_Dummy("x:1"))
        with pytest.raises(PermissionDeniedError, match="deny list"):
            r.acquire("x:0")
        # The other one is fine.
        assert r.acquire("x:1") is not None

    def test_allowlist_only(self):
        r = PeripheralRegistry(allowlist=frozenset({"x:1"}))
        r.register(_Dummy("x:0"))
        r.register(_Dummy("x:1"))
        with pytest.raises(PermissionDeniedError, match="allow list"):
            r.acquire("x:0")
        assert r.acquire("x:1") is not None

    def test_denylist_wins_over_allowlist(self):
        r = PeripheralRegistry(
            allowlist=frozenset({"x:0"}),
            denylist=frozenset({"x:0"}),
        )
        r.register(_Dummy("x:0"))
        with pytest.raises(PermissionDeniedError, match="deny list"):
            r.acquire("x:0")


class TestCapabilityHardware:
    def test_hardware_capability_exists(self):
        assert Capability.HARDWARE.value == "hardware"

    def test_parse_set_accepts_hardware(self):
        s = Capability.parse_set(["hardware", "network"])
        assert s == {Capability.HARDWARE, Capability.NETWORK}


class TestPeripheralBaseDefaults:
    def test_read_bytes_unsupported_by_default(self):
        d = _Dummy()
        with pytest.raises(PhantomError, match="not supported"):
            d.read_bytes()

    def test_write_bytes_unsupported_by_default(self):
        d = _Dummy()
        with pytest.raises(PhantomError, match="not supported"):
            d.write_bytes(b"x")
