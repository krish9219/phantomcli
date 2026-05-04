"""Tiny metrics primitives.

We keep this small and dependency-free. The :class:`Registry` is the
seam an operator uses to export to OpenTelemetry / Prometheus / etc.;
tests use :func:`REGISTRY.export()` directly.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field

__all__ = ["Counter", "Histogram", "REGISTRY", "Registry", "reset_for_tests"]


@dataclass
class Counter:
    """Monotonically increasing counter."""

    name: str
    _by_labels: dict[tuple[tuple[str, str], ...], int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, n: int = 1, **labels: str) -> None:
        if n < 0:
            raise ValueError("Counter.inc cannot decrease")
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._by_labels[key] = self._by_labels.get(key, 0) + n

    def value(self, **labels: str) -> int:
        key = tuple(sorted(labels.items()))
        with self._lock:
            return self._by_labels.get(key, 0)

    def export(self) -> list[dict]:
        out = []
        with self._lock:
            for k, v in self._by_labels.items():
                out.append({
                    "name": self.name, "value": v,
                    "labels": dict(k),
                })
        return out


@dataclass
class Histogram:
    """Histogram with caller-supplied bucket boundaries."""

    name: str
    buckets: tuple[float, ...] = (0.001, 0.01, 0.1, 1.0, 10.0)
    _counts: dict[tuple[tuple[str, str], ...], list[int]] = field(default_factory=dict)
    _sum: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            counts = self._counts.setdefault(key, [0] * (len(self.buckets) + 1))
            self._sum[key] = self._sum.get(key, 0.0) + value
            for i, edge in enumerate(self.buckets):
                if value <= edge:
                    counts[i] += 1
                    return
            counts[-1] += 1  # +Inf bucket

    def export(self) -> list[dict]:
        out = []
        with self._lock:
            for k, counts in self._counts.items():
                out.append({
                    "name": self.name,
                    "buckets": list(self.buckets) + [float("inf")],
                    "counts": list(counts),
                    "sum": self._sum.get(k, 0.0),
                    "labels": dict(k),
                })
        return out


@dataclass
class Registry:
    counters: dict[str, Counter] = field(default_factory=dict)
    histograms: dict[str, Histogram] = field(default_factory=dict)

    def counter(self, name: str) -> Counter:
        if name not in self.counters:
            self.counters[name] = Counter(name=name)
        return self.counters[name]

    def histogram(self, name: str, buckets: tuple[float, ...] | None = None) -> Histogram:
        if name not in self.histograms:
            self.histograms[name] = Histogram(
                name=name,
                buckets=buckets or (0.001, 0.01, 0.1, 1.0, 10.0),
            )
        return self.histograms[name]

    def export(self) -> dict:
        out: dict = {"counters": [], "histograms": []}
        for c in self.counters.values():
            out["counters"].extend(c.export())
        for h in self.histograms.values():
            out["histograms"].extend(h.export())
        return out


REGISTRY = Registry()


def reset_for_tests() -> None:
    REGISTRY.counters.clear()
    REGISTRY.histograms.clear()
