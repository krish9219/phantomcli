"""Tests for the bench command."""

from __future__ import annotations

from phantom.cli.bench import (
    BenchResult,
    _measure_daemon_roundtrip_ms,
    _measure_scaling,
    _measure_synthetic_turns,
    run_bench,
)


def test_bench_result_fields():
    r = run_bench(n_turns=5, n_agents_max=3)
    assert isinstance(r, BenchResult)
    assert r.version
    assert r.cold_start_ms > 0
    assert r.daemon_start_ms >= 0
    assert r.rss_mb_idle >= 0
    assert r.turn_latency_ms_p50 >= 0
    assert r.turn_latency_ms_p95 >= r.turn_latency_ms_p50
    assert r.n_turns == 5
    assert r.n_agents_max == 3


def test_synthetic_turn_p95_geq_p50():
    p50, p95 = _measure_synthetic_turns(n=20)
    assert p95 >= p50


def test_scaling_slope_is_finite():
    slope = _measure_scaling(n_max=5)
    assert slope == slope  # not NaN
    assert abs(slope) < 100.0  # sanity bound


def test_daemon_roundtrip_under_50ms():
    """The whole point of daemon mode."""
    elapsed_ms = _measure_daemon_roundtrip_ms()
    assert elapsed_ms < 50.0, f"daemon roundtrip {elapsed_ms}ms exceeds 50ms target"
