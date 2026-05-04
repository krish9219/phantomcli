"""Clock plugin — the simplest possible plugin.

No capabilities, no I/O. Returns ISO-8601 UTC. Useful as a smoke test
for the loader and as a copy-paste template.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from phantom.plugins.plugin import Plugin, PluginContext

__all__ = ["ClockPlugin"]


class ClockPlugin(Plugin):
    """Return the current wall-clock time as ISO-8601 UTC."""

    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        # `ctx` and `payload` are unused — the clock plugin has no inputs.
        return {
            "now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
