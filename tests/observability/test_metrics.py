"""Tests for :mod:`phantom.observability.metrics`."""

from __future__ import annotations

import math

import pytest

from phantom.observability import REGISTRY, reset_for_tests
from phantom.observability.metrics import Counter, Histogram


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    yield
    reset_for_tests()


class TestCounter:
    def test_inc_default(self):
        c = REGISTRY.counter("calls")
        c.inc()
        c.inc()
        assert c.value() == 2

    def test_inc_with_labels(self):
        c = REGISTRY.counter("calls")
        c.inc(channel="webchat")
        c.inc(channel="webchat")
        c.inc(channel="telegram")
        assert c.value(channel="webchat") == 2
        assert c.value(channel="telegram") == 1

    def test_inc_negative_rejected(self):
        c = REGISTRY.counter("x")
        with pytest.raises(ValueError):
            c.inc(-1)

    def test_export(self):
        c = REGISTRY.counter("x")
        c.inc(2, k="a")
        rows = c.export()
        assert rows == [{"name": "x", "value": 2, "labels": {"k": "a"}}]


class TestHistogram:
    def test_buckets_assignment(self):
        h = REGISTRY.histogram("latency", buckets=(0.01, 0.1, 1.0))
        h.observe(0.005)   # bucket 0
        h.observe(0.5)     # bucket 2
        h.observe(2.0)     # +Inf bucket
        rows = h.export()
        assert rows[0]["counts"] == [1, 0, 1, 1]
        assert math.isclose(rows[0]["sum"], 2.505)

    def test_default_buckets(self):
        h = REGISTRY.histogram("x")
        # The default buckets are 0.001..10
        assert 0.001 in h.buckets
        assert 10.0 in h.buckets


class TestRegistry:
    def test_export_combines(self):
        REGISTRY.counter("c").inc()
        REGISTRY.histogram("h").observe(0.05)
        out = REGISTRY.export()
        assert out["counters"]
        assert out["histograms"]

    def test_get_or_create_idempotent(self):
        c1 = REGISTRY.counter("foo")
        c2 = REGISTRY.counter("foo")
        assert c1 is c2
