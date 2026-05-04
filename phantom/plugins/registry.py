"""Persistent enable/disable state for plugins.

The registry stores a JSON file at
``$PHANTOM_HOME/plugin-registry.json`` listing each known plugin's
enabled status. Operators run ``phantom plugin disable <name>`` to
keep a plugin's directory present but hide it from the loader.

The registry does not hold the plugin instances — that's
:class:`PluginLoader`'s job. The two classes co-operate: the loader
discovers, the registry filters.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phantom.errors import PluginError

__all__ = ["PluginRegistry"]


def _registry_path() -> Path:
    base = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
    return Path(base) / "plugin-registry.json"


@dataclass
class PluginRegistry:
    """Persistent enable/disable state for known plugins.

    Loaded at construction time; mutated via :meth:`enable` /
    :meth:`disable`; persisted on every mutation.
    """

    path: Path
    _enabled: dict[str, bool]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PluginRegistry":
        """Load the registry; return an empty one if the file is absent."""
        target = Path(path) if path is not None else _registry_path()
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            return cls(path=target, _enabled={})
        try:
            data: Any = json.loads(target.read_text())
        except json.JSONDecodeError as exc:
            raise PluginError(f"plugin registry corrupted: {target}: {exc}") from exc
        if not isinstance(data, dict):
            raise PluginError(f"plugin registry must be a JSON object: {target}")
        # Coerce types — a hand-edited file might have wrong shapes.
        clean: dict[str, bool] = {}
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            clean[k] = bool(v)
        return cls(path=target, _enabled=clean)

    # ─── queries ───────────────────────────────────────────────────────

    def is_enabled(self, name: str) -> bool:
        """Return True iff *name* is enabled. Unknown plugins default to True."""
        return self._enabled.get(name, True)

    def known(self) -> list[str]:
        """Return the names the registry has an explicit record for."""
        return sorted(self._enabled)

    # ─── mutations ─────────────────────────────────────────────────────

    def enable(self, name: str) -> None:
        self._enabled[name] = True
        self._save()

    def disable(self, name: str) -> None:
        self._enabled[name] = False
        self._save()

    def forget(self, name: str) -> None:
        """Forget *name* — the next ``is_enabled(name)`` call returns the
        default. Used by ``phantom plugin uninstall``."""
        self._enabled.pop(name, None)
        self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._enabled, sort_keys=True, indent=2))
        os.chmod(self.path, 0o600)
