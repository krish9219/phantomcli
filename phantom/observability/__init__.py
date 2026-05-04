"""Phantom observability — typed counters, histograms, span emission.

Stage 8 ships a tiny in-process metrics registry. Operators who want to
export to OpenTelemetry / Prometheus install ``phantom-cli[otel]`` and
the registry's ``export()`` method emits compatible payloads.

The registry is global-but-resettable; tests reset between cases via
:func:`reset_for_tests`.
"""

from __future__ import annotations

from phantom.observability.metrics import (
    Counter,
    Histogram,
    Registry,
    REGISTRY,
    reset_for_tests,
)

__all__ = [
    "Counter",
    "Histogram",
    "REGISTRY",
    "Registry",
    "reset_for_tests",
]
