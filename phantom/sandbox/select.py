"""Sandbox backend selection.

The first call to :func:`select_backend` probes every registered backend
and caches the chosen one for the lifetime of the process. Operators can
pin a tier via the ``preferred`` argument (or ``PHANTOM_SANDBOX_TIER``
env var) and disable tiers with ``disabled``.

Selection algorithm
-------------------

1. If ``preferred`` is set and that backend probes available, use it.
2. Otherwise iterate :data:`phantom.sandbox.backends.ALL_BACKENDS` in
   tier-rank order; skip any backend on the ``disabled`` list; pick the
   first available one.
3. If none probe available, raise :class:`SandboxUnavailableError`.

The cache is keyed on ``(preferred, disabled)`` so tests that need to
re-probe can pass different keys without polluting the production cache.
"""

from __future__ import annotations

import os
from typing import Final

from phantom.errors import SandboxUnavailableError
from phantom.sandbox._backend import SandboxBackend
from phantom.sandbox.backends import all_backends

__all__ = [
    "PHANTOM_SANDBOX_TIER_ENV",
    "available_backends",
    "clear_cache",
    "select_backend",
]


PHANTOM_SANDBOX_TIER_ENV: Final[str] = "PHANTOM_SANDBOX_TIER"


# Module-level cache. Keys: (preferred, frozenset(disabled)).
_cache: dict[tuple[str | None, frozenset[str]], SandboxBackend] = {}


def clear_cache() -> None:
    """Drop the selection cache. Call from tests after monkeypatching backends."""
    _cache.clear()


def _resolve_preferred(preferred: str | None) -> str | None:
    """Honour the env var when no explicit override is given."""
    if preferred is not None:
        return preferred
    env = os.environ.get(PHANTOM_SANDBOX_TIER_ENV, "").strip()
    return env or None


def select_backend(
    *,
    preferred: str | None = None,
    disabled: frozenset[str] | None = None,
    backends: list[SandboxBackend] | None = None,
) -> SandboxBackend:
    """Return the highest-ranked available backend not in *disabled*.

    Parameters
    ----------
    preferred:
        If non-None and that backend's :meth:`probe` returns True, use it
        regardless of tier rank. ``"docker"``, ``"bwrap"``, etc.
    disabled:
        Frozen set of backend names to skip. Operator-driven; a CVE in
        firejail can be worked around by adding ``"firejail"`` here
        without changing code.
    backends:
        Override the registry, primarily for tests. Defaults to the live
        :func:`phantom.sandbox.backends.all_backends` registry.

    Raises
    ------
    SandboxUnavailableError
        If no acceptable backend is available on this host.
    """
    preferred = _resolve_preferred(preferred)
    disabled = disabled or frozenset()
    key = (preferred, disabled)
    if key in _cache:
        return _cache[key]

    pool = backends if backends is not None else all_backends()

    # Honour preference.
    if preferred:
        for b in pool:
            if b.name == preferred and b.name not in disabled:
                if b.probe():
                    _cache[key] = b
                    return b
                # Preferred backend was set but unavailable. Fall through
                # to the regular selection — better something than
                # failing closed.
                break

    for b in pool:
        if b.name in disabled:
            continue
        if b.probe():
            _cache[key] = b
            return b

    raise SandboxUnavailableError(
        "no sandbox backend available; install one of bubblewrap, firejail, "
        "or docker, or run on Linux ≥ 3.8 with user namespaces enabled"
    )


def available_backends(
    *, backends: list[SandboxBackend] | None = None
) -> list[SandboxBackend]:
    """Return every backend that is currently available on this host.

    Useful for ``phantom doctor`` and the dashboard's "diagnostics" panel.
    Does not honour the disabled list — this is a state report, not a
    selection.
    """
    pool = backends if backends is not None else all_backends()
    return [b for b in pool if b.probe()]
