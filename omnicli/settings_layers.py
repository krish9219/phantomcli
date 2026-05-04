"""
Layered settings hierarchy — mirrors Claude Code's settings.json layering.

Phantom already has `settings.py` (a static registry of known keys + defaults
used by the onboarding flow) and `memory.py` (SQLite-backed kv store). This
module sits on top as a resolution layer: given a key, walk four config
layers in priority order and return the winning value.

Layer order (lowest priority first — later layers override earlier):
  1. system  — /etc/phantom/settings.json   (admin/enterprise, can lock keys)
  2. user    — ~/.phantom/settings.json     (personal defaults)
  3. project_shared — ./.phantom/settings.json       (checked into the repo)
  4. project_local  — ./.phantom/settings.local.json (gitignored)

If the system layer declares `locked_keys: ["key1", "key2"]`, lower layers
cannot override those keys.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("omnicli.settings_layers")

_DEFAULT_SYSTEM_PATH = "/etc/phantom/settings.json"
_DEFAULT_USER_PATH   = os.path.expanduser("~/.phantom/settings.json")
_PROJECT_SHARED_BASENAME = os.path.join(".phantom", "settings.json")
_PROJECT_LOCAL_BASENAME  = os.path.join(".phantom", "settings.local.json")


def _system_path() -> str:
    return os.environ.get("PHANTOM_SYSTEM_SETTINGS", _DEFAULT_SYSTEM_PATH)


def _user_path() -> str:
    return os.environ.get("PHANTOM_USER_SETTINGS", _DEFAULT_USER_PATH)


def _project_root(start: Optional[str] = None) -> Optional[str]:
    cwd = Path(start or os.getcwd()).resolve()
    for p in (cwd, *cwd.parents):
        if (p / ".phantom").is_dir():
            return str(p)
    return None


def _project_shared_path(start: Optional[str] = None) -> Optional[str]:
    root = _project_root(start)
    return os.path.join(root, _PROJECT_SHARED_BASENAME) if root else None


def _project_local_path(start: Optional[str] = None) -> Optional[str]:
    root = _project_root(start)
    return os.path.join(root, _PROJECT_LOCAL_BASENAME) if root else None


def _read_layer(path: Optional[str]) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log.warning("settings file %s is not a JSON object", path)
            return {}
        return data
    except (OSError, json.JSONDecodeError) as e:
        log.warning("settings file %s unreadable: %s", path, e)
        return {}


SYSTEM = "system"
USER   = "user"
PROJECT_SHARED = "project_shared"
PROJECT_LOCAL  = "project_local"
_LAYER_ORDER = (SYSTEM, USER, PROJECT_SHARED, PROJECT_LOCAL)


@dataclass
class ResolvedSettings:
    values:  dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    locked:  set  = field(default_factory=set)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def source(self, key: str) -> str:
        return self.sources.get(key, "(default)")

    def is_locked(self, key: str) -> bool:
        return key in self.locked


def load(start: Optional[str] = None) -> ResolvedSettings:
    """Read all four layers, merge by priority, return the resolution."""
    sys_data    = _read_layer(_system_path())
    user_data   = _read_layer(_user_path())
    proj_shared = _read_layer(_project_shared_path(start))
    proj_local  = _read_layer(_project_local_path(start))
    layers = {
        SYSTEM: sys_data,
        USER:   user_data,
        PROJECT_SHARED: proj_shared,
        PROJECT_LOCAL:  proj_local,
    }
    raw_locked = sys_data.get("locked_keys", [])
    locked_keys = set(str(k) for k in raw_locked) if isinstance(raw_locked, list) else set()

    values:  dict = {}
    sources: dict = {}
    for layer_name in _LAYER_ORDER:
        data = layers[layer_name]
        for k, v in data.items():
            if k == "locked_keys":
                continue
            if k in locked_keys and layer_name != SYSTEM:
                continue
            values[k] = v
            sources[k] = layer_name
    return ResolvedSettings(values=values, sources=sources, locked=locked_keys)


def get(key: str, default: Any = None, start: Optional[str] = None) -> Any:
    return load(start=start).get(key, default)


def get_source(key: str, start: Optional[str] = None) -> str:
    return load(start=start).source(key)


def is_locked(key: str, start: Optional[str] = None) -> bool:
    return load(start=start).is_locked(key)


__all__ = [
    "load", "get", "get_source", "is_locked",
    "ResolvedSettings",
    "SYSTEM", "USER", "PROJECT_SHARED", "PROJECT_LOCAL",
]
