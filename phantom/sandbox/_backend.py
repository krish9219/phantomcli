"""Sandbox backend ABC — every tier conforms to this interface.

Backends are stateless adapters that translate a :class:`SandboxPolicy`
plus an argv into a launch on a particular host facility (bwrap,
firejail, unshare, docker). They must:

* declare a :attr:`name` and a :attr:`tier_rank`,
* report their availability via :meth:`probe`,
* execute a command via :meth:`launch` and return a :class:`SandboxResult`.

Backends do not know about the audit log, the selection logic, or the
per-call timing instrumentation; those concerns live one layer up in
:func:`phantom.sandbox.run`. This separation keeps each backend's surface
narrow and testable.

Implementations must be **subprocess-only** — backends are the one place
in :mod:`phantom` allowed to call into ``subprocess.*``. The grep test
in ``tests/sandbox/test_no_unsandboxed_subprocess.py`` enforces it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Final

from phantom.sandbox.policy import SandboxPolicy
from phantom.sandbox.result import SandboxResult

__all__ = ["SandboxBackend"]


class SandboxBackend(ABC):
    """Abstract base for sandbox backends.

    Subclasses must override :attr:`name`, :attr:`tier_rank`,
    :meth:`probe`, and :meth:`launch`. Anything else they expose is
    considered private.

    Tier ranks (lower = preferred):

    * 1 — bubblewrap (lightest, most-supported)
    * 2 — firejail
    * 3 — unshare + prlimit (kernel-only, always-available on Linux)
    * 4 — docker (heaviest, required on non-Linux)
    """

    name: Final[str]
    tier_rank: Final[int]

    @abstractmethod
    def probe(self) -> bool:
        """Return True iff this backend is usable on the current host.

        Implementations should perform the cheapest possible check —
        typically running ``<tool> --version`` with a short timeout. The
        result is cached by :func:`phantom.sandbox.select_backend`; this
        method is allowed to be slow but should not be slower than
        ~50 ms in the success case.

        Probes must never raise. Any exception is interpreted as
        "unavailable".
        """

    @abstractmethod
    def launch(self, argv: list[str], policy: SandboxPolicy) -> SandboxResult:
        """Execute *argv* under *policy* and return the result.

        Raises
        ------
        phantom.errors.SandboxUnavailableError
            If the backend disappeared between probe and launch (rare).
        phantom.errors.SandboxLaunchError
            If the backend exec'd but failed before the command ran.
        phantom.errors.SandboxTimeoutError
            If the wall-clock deadline was exceeded.
        phantom.errors.SandboxResourceError
            If a resource ceiling tripped before completion.
        phantom.errors.SandboxOutputTruncatedError
            Only if the policy's ``raise_on_truncation`` flag is set.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} tier={self.tier_rank}>"
