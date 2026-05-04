"""Tests for :mod:`phantom.auth.pool`."""

from __future__ import annotations

import pytest

from phantom.auth import KeyPool, KeyPoolEmptyError
from phantom.errors import PhantomError


class TestPoolBasics:
    def test_round_robin(self):
        p = KeyPool.from_keys("anthropic", ["k1", "k2", "k3"])
        assert p.checkout(now=10).key == "k1"
        assert p.checkout(now=11).key == "k2"
        assert p.checkout(now=12).key == "k3"
        assert p.checkout(now=13).key == "k1"

    def test_empty_pool_raises(self):
        p = KeyPool.from_keys("x", [])
        with pytest.raises(KeyPoolEmptyError, match="empty"):
            p.checkout()

    def test_from_keys_caps_at_64(self):
        with pytest.raises(PhantomError, match="64"):
            KeyPool.from_keys("x", [f"k{i}" for i in range(70)])


class TestCooldown:
    def test_cooled_key_skipped(self):
        p = KeyPool.from_keys("x", ["k1", "k2"])
        p.checkout(now=10)
        p.mark_failure("k1", cooldown_s=30, now=10)
        # All subsequent checkouts should yield k2 until cooldown ends.
        assert p.checkout(now=11).key == "k2"
        assert p.checkout(now=20).key == "k2"

    def test_all_cooling_raises(self):
        p = KeyPool.from_keys("x", ["k1", "k2"])
        p.mark_failure("k1", cooldown_s=30, now=10)
        p.mark_failure("k2", cooldown_s=30, now=10)
        with pytest.raises(KeyPoolEmptyError, match="cooldown"):
            p.checkout(now=20)

    def test_cooldown_expires(self):
        p = KeyPool.from_keys("x", ["k1"])
        p.mark_failure("k1", cooldown_s=10, now=100)
        with pytest.raises(KeyPoolEmptyError):
            p.checkout(now=105)
        # After the cooldown expires, the key is available again.
        assert p.checkout(now=120).key == "k1"


class TestSuccessResetsFailures:
    def test_success_clears_failure_count(self):
        p = KeyPool.from_keys("x", ["k1"])
        p.mark_failure("k1", cooldown_s=0, now=0)
        p.mark_failure("k1", cooldown_s=0, now=1)
        # Failures==2 in the entry; success resets.
        snap_before = p.stats()[0]
        assert snap_before["failures"] == 2
        p.mark_success("k1")
        snap_after = p.stats()[0]
        assert snap_after["failures"] == 0


class TestStats:
    def test_key_suffix_only(self):
        p = KeyPool.from_keys("x", ["sk-abcd1234efgh"])
        snap = p.stats()
        assert snap[0]["key_suffix"] == "efgh"
        # Full key is NOT exposed.
        assert "sk-abcd" not in str(snap)
