"""Tests for executor.god_mode_active / effective_trust TTL semantics."""
from __future__ import annotations

import time

import pytest

from omnicli import executor
from omnicli.memory import save_config, get_config


class TestGodModeTTL:
    def test_inactive_when_not_activated(self):
        assert executor.god_mode_active() is False

    def test_active_immediately_after_activation(self):
        executor.mark_god_mode_activated()
        assert executor.god_mode_active() is True

    def test_still_active_within_ttl(self, monkeypatch):
        executor.mark_god_mode_activated()
        # Simulate the TTL being huge so we're well within it.
        monkeypatch.setattr(executor, "_god_mode_ttl_s", lambda: 10_000)
        assert executor.god_mode_active() is True

    def test_inactive_after_ttl_expiry(self, monkeypatch):
        # Stamp a time well in the past.
        save_config("god_mode_activated_at", str(int(time.time()) - 3600))
        # TTL is 30 min default — 1 hour ago is past it.
        assert executor.god_mode_active() is False

    def test_ttl_expiry_clears_stamp(self, monkeypatch):
        """The second-call bug the audit claimed exists — verify it doesn't.
        Expired stamp must be cleared so later calls see an empty stamp."""
        save_config("god_mode_activated_at", str(int(time.time()) - 3600))
        assert executor.god_mode_active() is False
        # Stamp should now be empty (our fix eagerly clears it).
        assert get_config("god_mode_activated_at", "") == ""

    def test_effective_trust_downgrades_expired_four_to_three(self):
        save_config("god_mode_activated_at", str(int(time.time()) - 3600))
        assert executor.effective_trust(4) == 3

    def test_effective_trust_preserves_active_four(self):
        executor.mark_god_mode_activated()
        assert executor.effective_trust(4) == 4

    def test_effective_trust_passthrough_for_lower_levels(self):
        for t in (1, 2, 3):
            assert executor.effective_trust(t) == t

    def test_malformed_stamp_treated_as_inactive(self):
        save_config("god_mode_activated_at", "not-a-number")
        assert executor.god_mode_active() is False

    def test_ttl_config_override_respected(self, monkeypatch):
        """Custom god_mode_ttl_s should be picked up."""
        save_config("god_mode_ttl_s", "120")  # 2 minutes
        save_config("god_mode_activated_at", str(int(time.time()) - 300))  # 5 min ago
        assert executor.god_mode_active() is False

    def test_ttl_config_floor_enforced(self):
        """Silly-small TTLs are clamped to a safe floor (60s)."""
        save_config("god_mode_ttl_s", "1")
        # _god_mode_ttl_s has a max(60, ...) floor
        assert executor._god_mode_ttl_s() == 60
