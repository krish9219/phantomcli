"""Stage 8 smoke test."""

from __future__ import annotations

from pathlib import Path

import pytest

from phantom.auth import KeyPool
from phantom.observability import REGISTRY, reset_for_tests
from phantom.release import audit_repo


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.mark.stage8
def test_keypool_round_robin_and_cooldown():
    p = KeyPool.from_keys("anthropic", ["k1", "k2"])
    a = p.checkout(now=10).key
    b = p.checkout(now=11).key
    assert {a, b} == {"k1", "k2"}
    p.mark_failure(a, cooldown_s=30, now=20)
    # The next checkout under cooldown returns the other key.
    assert p.checkout(now=21).key != a


@pytest.mark.stage8
def test_metrics_export_is_well_shaped():
    REGISTRY.counter("phantom.calls").inc(2, tier="bwrap")
    REGISTRY.histogram("phantom.latency").observe(0.05)
    out = REGISTRY.export()
    assert any(c["name"] == "phantom.calls" for c in out["counters"])
    assert any(h["name"] == "phantom.latency" for h in out["histograms"])


@pytest.mark.stage8
def test_release_audit_passes_on_real_repo():
    # All closed stages have their peer-review + smoke test by now.
    issues = audit_repo(REPO_ROOT)
    assert issues == [], f"release blocked by: {issues}"


@pytest.mark.stage8
def test_phantom_stage_advanced_to_8():
    import phantom
    assert phantom.feature_flags()["stage"] == 8
