"""Plugin capability declarations.

A plugin lists the capabilities it needs in its manifest. The loader
materialises those into a :class:`phantom.sandbox.SandboxPolicy` so the
plugin runs with exactly the privileges it asked for — no more.

Capabilities are coarse on purpose. Fine-grained policy (specific
hostnames, specific filesystem paths) belongs in operator config, not
in a plugin manifest.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["Capability"]


class Capability(str, Enum):
    """The set of permissions a plugin can request.

    Members
    -------
    NETWORK:
        Outbound network egress is permitted. Plugins without this
        capability run in a sealed network namespace.
    EXECUTOR:
        The plugin may invoke shell commands via
        :func:`phantom.engine.execute_bash`. Without this, plugins are
        pure-Python.
    MEMORY:
        The plugin may read and write the per-session memory store.
        Without this, plugins are stateless across calls.
    FILESYSTEM:
        The plugin may read host filesystem paths declared in operator
        config. Plugins without this see only their own workspace.
    HARDWARE:
        The plugin may acquire :class:`phantom.hardware.Peripheral`
        instances from the registry. The registry's allowlist /
        denylist still gates *which* peripherals the plugin reaches.
    """

    NETWORK = "network"
    EXECUTOR = "executor"
    MEMORY = "memory"
    FILESYSTEM = "filesystem"
    HARDWARE = "hardware"

    @classmethod
    def parse_set(cls, raw: list[str] | tuple[str, ...]) -> "frozenset[Capability]":
        """Parse a list of capability names from a manifest. Raises on
        unknown names. Empty input → empty set.
        """
        out: set[Capability] = set()
        for name in raw:
            try:
                out.add(cls(name))
            except ValueError as exc:
                allowed = sorted(c.value for c in cls)
                raise ValueError(
                    f"unknown capability {name!r}; allowed: {allowed}"
                ) from exc
        return frozenset(out)
