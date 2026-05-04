"""Plugin base class.

Every plugin subclasses :class:`Plugin` and overrides :meth:`call`.
The lifecycle hooks (:meth:`activate`, :meth:`deactivate`) are optional;
their default implementations are no-ops.

The :class:`PluginContext` passed into :meth:`call` carries the loader's
view of the world: the active sandbox policy, a logger, an optional
memory handle, and the plugin's own working directory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phantom.plugins.capability import Capability
from phantom.plugins.manifest import PluginManifest
from phantom.sandbox import SandboxPolicy

__all__ = ["Plugin", "PluginContext"]


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Per-call context handed to a plugin.

    Plugins must not mutate the context object. The loader builds a
    fresh one for every :meth:`Plugin.call` invocation.

    Attributes
    ----------
    workdir:
        Read-write directory the plugin can use. Owned by the loader;
        mounted into the sandbox if the plugin uses the executor.
    sandbox_policy:
        The :class:`SandboxPolicy` the loader will pass to
        :func:`phantom.sandbox.run` for any executor calls this plugin
        makes. Read-only; plugins inspect but cannot rewrite it.
    capabilities:
        The set of capabilities the plugin was granted. Always a subset
        of the manifest's declared capabilities; an operator may have
        revoked some.
    manifest:
        The plugin's manifest (already validated).
    extras:
        Free-form per-call data the loader may pass through (e.g. a
        memory backend handle, an HTTP client). Plugin authors should
        consult their plugin's documentation for the keys present here.
    """

    workdir: Path
    sandbox_policy: SandboxPolicy
    capabilities: frozenset[Capability]
    manifest: PluginManifest
    extras: dict[str, Any]


class Plugin(ABC):
    """Abstract base for Phantom plugins.

    Subclass and implement :meth:`call`. Optional hooks
    (:meth:`activate`, :meth:`deactivate`) let plugins prepare and
    tear down expensive resources around an active session.
    """

    #: The plugin's manifest. Set by :meth:`__init__` from the loader.
    manifest: PluginManifest

    def __init__(self, manifest: PluginManifest) -> None:
        self.manifest = manifest

    # ─── lifecycle hooks ───────────────────────────────────────────────

    def activate(self) -> None:  # pragma: no cover — default no-op
        """Called once when the plugin is loaded into a session.

        Use this to open expensive resources (e.g. an HTTP client). The
        loader catches and logs any exception raised here, then marks
        the plugin as inactive.
        """
        return None

    def deactivate(self) -> None:  # pragma: no cover — default no-op
        """Called when the session that loaded this plugin shuts down."""
        return None

    # ─── the actual entry point ────────────────────────────────────────

    @abstractmethod
    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the plugin against *payload*.

        Parameters
        ----------
        ctx:
            Per-call :class:`PluginContext` (sandbox policy, capabilities,
            workdir, …).
        payload:
            JSON-serialisable input. The schema is plugin-defined; the
            loader does not validate it.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable result. The loader logs the result hash
            (not the contents) for the audit trail.

        Raises
        ------
        Any subclass of :class:`phantom.errors.PluginError`.
            The loader catches every exception, wraps it in a
            :class:`phantom.errors.PluginError`, and surfaces it to the
            caller without leaking the plugin's internal state.
        """

    # ─── helper for subclasses ─────────────────────────────────────────

    def has(self, cap: Capability) -> bool:
        """Convenience: True iff the plugin's manifest declares *cap*.

        Subclasses that branch on capabilities use this rather than
        re-implementing the membership check.
        """
        return cap in self.manifest.capabilities
