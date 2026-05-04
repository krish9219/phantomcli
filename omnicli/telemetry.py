"""
OpenTelemetry tracing + metrics — wraps model calls, tool dispatch, and
hook fires in spans with a `phantom.*` prefix.

Design principles:
  * Zero hard dependency. If `opentelemetry` isn't installed, all APIs
    are no-ops. Tests should still pass.
  * One-time initialization. Calling `init()` twice is safe.
  * Export target is env-driven (OTEL_EXPORTER_OTLP_ENDPOINT or
    PHANTOM_OTEL_EXPORTER). Defaults to a no-op exporter in production
    (so nothing is sent unless explicitly configured).
  * Tests import `memory_exporter` directly and assert span shapes.

Public API:
  * init(service_name="phantom")    — idempotent setup
  * span(name, **attrs)             — context manager
  * record_model_call(...)          — convenience wrapper for model calls
  * record_tool_call(...)           — convenience wrapper for tool dispatch
  * record_hook(...)                — convenience wrapper for hook fires
  * record_metric(name, value, **kwargs) — counter or gauge
  * shutdown()                      — flush + close exporter
"""
from __future__ import annotations

import contextlib
import logging
import os
import time
from typing import Any, Optional

log = logging.getLogger("omnicli.telemetry")

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        SimpleSpanProcessor, ConsoleSpanExporter, BatchSpanProcessor,
    )
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    _OTEL_OK = True
except ImportError:
    _OTEL_OK = False


# TracerProvider can only be set once per process — OTel's set_tracer_provider
# silently ignores subsequent calls. So we keep the provider around and only
# swap the exporter on each init/shutdown. This keeps tests reliable.
_memory_exporter: Optional[Any] = None
_tracer: Optional[Any] = None
_provider: Optional[Any] = None
_initialized = False
_metrics_buffer: list[dict] = []


def is_enabled() -> bool:
    return _OTEL_OK and _initialized


def init(
    service_name: str = "phantom",
    exporter: Optional[str] = None,
) -> bool:
    """Idempotent setup. Returns True if OTel was initialized (or was
    already initialized), False if opentelemetry isn't installed."""
    global _memory_exporter, _tracer, _provider, _initialized
    if not _OTEL_OK:
        log.debug("opentelemetry not installed — telemetry is a no-op")
        return False

    # Reuse the provider on re-init so tests don't leak spans across each other.
    if _provider is None:
        _provider = TracerProvider()
        trace.set_tracer_provider(_provider)

    target = exporter or os.environ.get("PHANTOM_OTEL_EXPORTER", "memory")

    # Clear any existing span processors so a fresh exporter is wired in.
    try:
        # TracerProvider keeps processors in _active_span_processor._span_processors
        sp = getattr(_provider, "_active_span_processor", None)
        if sp is not None:
            try:
                procs = list(getattr(sp, "_span_processors", []))
            except Exception:
                procs = []
            for p in procs:
                try:
                    p.shutdown()
                except Exception:
                    pass
            # Reset the internal list
            try:
                sp._span_processors = tuple() if isinstance(getattr(sp, "_span_processors", []), tuple) else []
            except Exception:
                pass
    except Exception:
        pass

    # Now add the fresh processor
    if target == "console":
        _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        _memory_exporter = None
    elif target == "memory":
        _memory_exporter = InMemorySpanExporter()
        _provider.add_span_processor(SimpleSpanProcessor(_memory_exporter))
    elif target == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            _memory_exporter = None
        except ImportError:
            log.warning("OTLP exporter requested but opentelemetry-exporter-otlp-proto-grpc "
                        "not installed — falling back to memory exporter")
            _memory_exporter = InMemorySpanExporter()
            _provider.add_span_processor(SimpleSpanProcessor(_memory_exporter))

    _tracer = trace.get_tracer(service_name)
    _initialized = True
    return True


def memory_exporter():
    """Return the in-memory exporter (tests only). None if not initialized."""
    return _memory_exporter


def shutdown() -> None:
    """Clear pending spans + mark uninitialized. Does NOT destroy the
    global TracerProvider (which OTel can only set once per process)."""
    global _initialized, _tracer, _memory_exporter
    if _memory_exporter is not None:
        try:
            _memory_exporter.clear()
        except Exception:
            pass
    _tracer = None
    _initialized = False


# ─── Span helpers ────────────────────────────────────────────────────────────


@contextlib.contextmanager
def span(name: str, **attrs):
    """Context manager yielding a span. No-op when OTel isn't configured."""
    if not is_enabled() or _tracer is None:
        yield _NullSpan()
        return
    with _tracer.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            try:
                sp.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
            except Exception:
                pass
        yield sp


class _NullSpan:
    def set_attribute(self, *a, **kw): pass
    def record_exception(self, *a, **kw): pass
    def set_status(self, *a, **kw): pass


# ─── Convenience wrappers ────────────────────────────────────────────────────


def record_model_call(model: str, prompt_tokens: int = 0,
                      completion_tokens: int = 0, duration_ms: float = 0.0,
                      provider: str = "unknown", error: str = "") -> None:
    """Emit a one-off span describing a completed model call."""
    if not is_enabled() or _tracer is None:
        return
    with _tracer.start_as_current_span("phantom.model.call") as sp:
        sp.set_attribute("model",              model)
        sp.set_attribute("provider",           provider)
        sp.set_attribute("prompt_tokens",      prompt_tokens)
        sp.set_attribute("completion_tokens",  completion_tokens)
        sp.set_attribute("total_tokens",       prompt_tokens + completion_tokens)
        sp.set_attribute("duration_ms",        duration_ms)
        if error:
            sp.set_attribute("error", error)


def record_tool_call(tool: str, trust: int, ok: bool,
                     duration_ms: float = 0.0, error: str = "") -> None:
    if not is_enabled() or _tracer is None:
        return
    with _tracer.start_as_current_span("phantom.tool.call") as sp:
        sp.set_attribute("tool",        tool)
        sp.set_attribute("trust",       trust)
        sp.set_attribute("ok",          ok)
        sp.set_attribute("duration_ms", duration_ms)
        if error:
            sp.set_attribute("error", error)


def record_hook(event: str, allowed: bool, exit_code: int,
                duration_ms: float = 0.0) -> None:
    if not is_enabled() or _tracer is None:
        return
    with _tracer.start_as_current_span("phantom.hook.fire") as sp:
        sp.set_attribute("event",      event)
        sp.set_attribute("allowed",    allowed)
        sp.set_attribute("exit_code",  exit_code)
        sp.set_attribute("duration_ms", duration_ms)


# ─── Lightweight metrics (no Prometheus dep; just a buffer) ──────────────────


def record_metric(name: str, value: float, **labels) -> None:
    """Append a metric event. Simple enough for now — later upgrade to
    real OTel metrics once the meter API stabilises in our SDK pin."""
    _metrics_buffer.append({
        "ts":     time.time(),
        "name":   name,
        "value":  float(value),
        "labels": dict(labels),
    })
    # Ring-buffer to avoid unbounded growth
    if len(_metrics_buffer) > 10_000:
        del _metrics_buffer[:5_000]


def metrics_snapshot() -> list[dict]:
    return list(_metrics_buffer)


def clear_metrics() -> None:
    _metrics_buffer.clear()


__all__ = [
    "init", "shutdown", "is_enabled",
    "span",
    "record_model_call", "record_tool_call", "record_hook",
    "record_metric", "metrics_snapshot", "clear_metrics",
    "memory_exporter",
]
