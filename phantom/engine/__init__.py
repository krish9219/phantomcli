"""Phantom engine — the agent loop and tool dispatch surface.

In v3 (`omnicli` package), the engine is a single 1000+ line module.
The v4 engine is being broken out incrementally. Stage 1 lands the
sandboxed executor only; later stages bring the rest of the engine
across.

Public surface (Stage 1)
------------------------

:class:`ExecuteBashRequest` — typed request to the executor.
:class:`ExecuteBashResult` — typed response.
:func:`execute_bash` — sandbox-mediated bash execution.

The v3 ``omnicli.executor.execute_bash`` keeps working; v4 callers use
this module instead. Migration of v3 internals to v4 is a Stage-8
deliverable.
"""

from __future__ import annotations

from phantom.engine.executor import (
    ExecuteBashRequest,
    ExecuteBashResult,
    execute_bash,
)

__all__ = [
    "ExecuteBashRequest",
    "ExecuteBashResult",
    "execute_bash",
]
