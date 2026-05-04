"""Phantom — production-grade local AI agent.

Phantom v4 is the successor to PhantomCLI v3 (`omnicli` package). The two
packages cohabit during the v4 development cycle:

* ``phantom`` — new code lands here. Strict typing, branch-coverage gate,
  open-core surface.
* ``omnicli`` — frozen v3 surface, kept passing for the existing 796-test
  baseline. Re-exports a curated subset of ``phantom`` once stage migrations
  retire each module.

Public API
----------

The top-level ``phantom`` namespace re-exports the small set of symbols every
caller needs. Sub-modules are imported lazily to keep ``phantom`` cheap to
import — a CLI that only renders ``--version`` should not pay the cost of
loading FastAPI, Playwright, or model SDKs.

Examples
--------

>>> import phantom
>>> phantom.__version__
'4.0.0-dev'
>>> phantom.feature_flags()['stage']
0
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from phantom._version import RELEASE_DATE, VERSION_TUPLE, __version__

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from types import ModuleType

__all__ = [
    "RELEASE_DATE",
    "VERSION_TUPLE",
    "__version__",
    "feature_flags",
]

# ─── Stage tracking ───────────────────────────────────────────────────────────
# Bumped at the end of each stage's peer review. Tests assert this matches the
# highest STAGE_<N>.md file marked DONE. Used by ``phantom doctor`` and the
# dashboard's "what's enabled" panel.
_CURRENT_STAGE: int = 8


def feature_flags() -> dict[str, Any]:
    """Return a snapshot of compile-time/runtime feature flags.

    Stable, machine-readable. The dashboard, the ``phantom doctor`` command,
    and the test suite all consume this dict.
    """
    return {
        "stage": _CURRENT_STAGE,
        "version": __version__,
        "release_date": RELEASE_DATE,
    }


# Lazy module loading. Sub-packages register themselves into _LAZY_MODULES
# when their stage lands; until then attribute access raises AttributeError
# with a clear "not yet implemented at stage N" message.
_LAZY_MODULES: dict[str, str] = {
    # name -> dotted module path. Populated by later stages.
}


def __getattr__(name: str) -> "ModuleType":
    """Lazily resolve sub-modules registered by later stages."""
    if name in _LAZY_MODULES:
        return import_module(_LAZY_MODULES[name])
    raise AttributeError(
        f"module 'phantom' has no attribute {name!r} "
        f"(current stage={_CURRENT_STAGE}; check docs/stages/ for the roadmap)"
    )
