"""Sandbox backend implementations.

Each backend module exports exactly one subclass of
:class:`phantom.sandbox._backend.SandboxBackend`. Importing a backend
module **must not** require the backend to be installed — the ``probe``
method is responsible for declaring availability at runtime.

The :data:`ALL_BACKENDS` list is the ordered registry consumed by
:mod:`phantom.sandbox.select`. Lower :attr:`tier_rank` is preferred.
"""

from __future__ import annotations

from phantom.sandbox._backend import SandboxBackend
from phantom.sandbox.backends.bwrap import BwrapBackend
from phantom.sandbox.backends.docker import DockerBackend
from phantom.sandbox.backends.firejail import FirejailBackend
from phantom.sandbox.backends.passthrough import PassthroughBackend
from phantom.sandbox.backends.unshare import UnshareBackend

__all__ = [
    "ALL_BACKENDS",
    "BwrapBackend",
    "DockerBackend",
    "FirejailBackend",
    "PassthroughBackend",
    "SandboxBackend",
    "UnshareBackend",
    "all_backends",
]


def all_backends() -> list[SandboxBackend]:
    """Return a fresh list of every known backend, sorted by tier rank.

    A function (not a constant) so tests can monkeypatch the registry
    without leaking into other tests.
    """
    return sorted(
        (
            BwrapBackend(),
            FirejailBackend(),
            UnshareBackend(),
            DockerBackend(),
            PassthroughBackend(),  # rank 99 — last-resort fallback for Windows
        ),
        key=lambda b: b.tier_rank,
    )


# Module-level constant for the common case where mutation is not needed.
ALL_BACKENDS: list[SandboxBackend] = all_backends()
