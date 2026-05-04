"""
Tool dispatch facade — consolidates schema validation, permission check,
pre/post hooks, and tool execution behind one callable.

This is the step from the roadmap's "engine decomposition" item. We keep
`generate_response` in engine.py as the orchestrator, but all NEW code
(and refactored call sites) should go through this module instead of
reaching into the private `engine._execute_tool`.

Why a wrapper rather than the raw function?
  * Gives us a single place to add cross-cutting concerns later
    (OTel tracing, metrics, retry policy) without touching engine.py.
  * Lets tests patch this module's symbols cleanly.
  * Names are cleaner: `dispatch` vs `_execute_tool`.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


def dispatch(
    name:  str,
    args:  dict,
    trust: int,
    on_bash_output: Optional[Callable[[str], None]] = None,
    tracker: Any = None,
) -> str:
    """Validate → pre-hook → execute → post-hook the named tool.

    Returns the tool's output string (or a structured error string the
    model can interpret and retry against).

    Every call is wrapped in a `phantom.tool.call` OTel span (no-op if
    telemetry isn't initialised) so downstream observability systems see
    tool usage + duration + success without any engine-level change.
    """
    import time
    from omnicli import engine
    try:
        from omnicli import telemetry as _tel
    except ImportError:
        _tel = None

    t0 = time.perf_counter()
    out: str = ""
    ok = False
    err = ""
    try:
        span_cm = _tel.span("phantom.tool.call",
                            **{"tool": name, "trust": trust}) if _tel else _NullCtx()
        with span_cm:
            out = engine._execute_tool(
                name=name,
                args=args,
                trust=trust,
                on_bash_output=on_bash_output,
                tracker=tracker,
            )
        # Classify success: not an INVALID_TOOL_ARGS / HOOK_BLOCKED / Unknown-tool error
        if isinstance(out, str):
            bad_prefixes = ("INVALID_TOOL_ARGS", "HOOK_BLOCKED", "Unknown tool", "ERROR:", "DENIED:", "BLOCKED:")
            ok = not out.startswith(bad_prefixes)
            if not ok:
                # Take the first line of the error string for the span attribute
                err = out.split("\n", 1)[0][:200]
        else:
            ok = True
        return out
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000
        if _tel:
            try:
                _tel.record_tool_call(
                    tool=name, trust=trust, ok=ok,
                    duration_ms=duration_ms, error=err,
                )
                _tel.record_metric("phantom.tool.duration_ms", duration_ms,
                                   tool=name, ok=str(ok).lower())
            except Exception:
                pass


class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *a, **kw): return False
    def set_attribute(self, *a, **kw): pass


__all__ = ["dispatch"]
