"""Phantom browser automation.

Two complementary surfaces ship in v1.0:

1. **Granular primitive tool** (:class:`Browser`) — nine low-level ops
   (navigate / click / type / wait / snapshot / screenshot / eval_js /
   scroll / close) the agent calls one at a time. Useful when the agent
   wants explicit control of the browse loop, with structured
   :class:`BrowserResult` returned from every call. Backend is
   pluggable: :class:`PlaywrightBackend` for production,
   :class:`StubBackend` for tests.

2. **High-level task runner** (:class:`BrowserAgentRunner`) — a thin
   wrapper around the MIT-licensed `browser-use` library that runs an
   end-to-end task to completion. Useful when the agent wants to
   delegate the whole browse-and-act loop in one shot.

Operators choose: low-level for surgical browse, high-level for
"figure it out yourself" tasks. Both share the Playwright Chromium
runtime so installing one installs the other.
"""

from __future__ import annotations

from phantom.browser.tool import (
    Browser,
    BrowserBackend,
    BrowserError,
    BrowserResult,
    PlaywrightBackend,
    StubBackend,
)

# The high-level task runner depends on the optional `browser-use`
# library; import lazily so `from phantom.browser import Browser`
# stays cheap and works without that dep.
try:
    from phantom.browser.runner import (  # noqa: F401
        BrowserAgentRunner,
        BrowserTaskResult,
        browser_task_tool,
    )
    _HAS_RUNNER = True
except Exception:  # pragma: no cover — runner missing is fine
    _HAS_RUNNER = False

__all__ = [
    "Browser",
    "BrowserBackend",
    "BrowserError",
    "BrowserResult",
    "PlaywrightBackend",
    "StubBackend",
]

if _HAS_RUNNER:
    __all__ += ["BrowserAgentRunner", "BrowserTaskResult", "browser_task_tool"]
