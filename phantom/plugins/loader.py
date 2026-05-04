"""Plugin loader.

Scans a directory of plugins, validates each manifest, imports the entry
point, and instantiates the :class:`Plugin` subclass with its manifest.
The loader is the only place that imports plugin code, so plugin
authors can be confident their module loads exactly once per session.

Discovery
---------

Two layouts are supported:

1. **Bundled plugins.** ``phantom/plugins/builtin/<name>/`` contains
   ``manifest.json`` and ``__init__.py``. They ship inside the wheel
   and load by default.
2. **User plugins.** ``$PHANTOM_HOME/plugins/<name>/`` (default
   ``~/.phantom/plugins/<name>/``) — same layout. Discovered at
   :meth:`PluginLoader.discover` time.

The loader does **not** install plugins from PyPI in Stage 2. Stage-8
adds ``phantom plugin install <package>``; today the operator places
the directory by hand or via ``phantom plugin install <path>``.
"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from phantom.errors import PluginError
from phantom.plugins.manifest import PluginManifest
from phantom.plugins.plugin import Plugin
from phantom.plugins.signature import verify_signature

__all__ = ["PluginLoader", "load_plugin", "user_plugins_dir"]

log = logging.getLogger(__name__)


def user_plugins_dir() -> Path:
    """Return the per-user plugins directory.

    Honours ``PHANTOM_HOME``; defaults to ``~/.phantom/plugins``.
    Created with mode 0700 if missing.
    """
    base = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
    p = Path(base) / "plugins"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def builtin_plugins_dir() -> Path:
    """Return the builtin plugins directory shipped inside the wheel."""
    return Path(__file__).resolve().parent / "builtin"


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """A discovered + instantiated plugin, ready for use."""

    manifest: PluginManifest
    instance: Plugin
    signed: bool   # True iff the manifest carried a verified signature.
    source_dir: Path


def _load_manifest_from_dir(directory: Path) -> PluginManifest:
    """Load and validate ``<directory>/manifest.json``."""
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise PluginError(f"no manifest.json in {directory}")
    return PluginManifest.load(manifest_path)


def _import_entry_point(entry_point: str) -> type[Plugin]:
    """Import ``module:Class`` and return the class object.

    Raises :class:`PluginError` on import failure or if the class does
    not subclass :class:`Plugin`.
    """
    try:
        module_name, class_name = entry_point.split(":", 1)
    except ValueError as exc:
        raise PluginError(
            f"entry_point {entry_point!r} must be 'module:Class'"
        ) from exc
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise PluginError(
            f"entry_point {entry_point!r} module {module_name!r} could not be imported: {exc}"
        ) from exc
    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise PluginError(
            f"entry_point {entry_point!r}: {module_name!r} has no attribute {class_name!r}"
        ) from exc
    if not (isinstance(cls, type) and issubclass(cls, Plugin)):
        raise PluginError(
            f"entry_point {entry_point!r} must resolve to a Plugin subclass"
        )
    return cls


def load_plugin(directory: str | Path) -> LoadedPlugin:
    """Load a single plugin from a directory.

    Errors raise :class:`PluginError` and never leak partially-constructed
    plugin instances. The signature is verified if present; absence is
    not an error here (operator policy decides whether to refuse
    unsigned plugins; see ``PluginLoader.discover`` for the policy
    layer).
    """
    d = Path(directory)
    manifest = _load_manifest_from_dir(d)
    cls = _import_entry_point(manifest.entry_point)
    try:
        instance = cls(manifest=manifest)
    except Exception as exc:
        raise PluginError(
            f"plugin {manifest.name!r} failed to instantiate: {exc}"
        ) from exc

    signed = False
    if manifest.signature is not None:
        # `verify_signature` raises on tampering; absence is False, not raise.
        signed = verify_signature(manifest.to_dict())

    return LoadedPlugin(
        manifest=manifest,
        instance=instance,
        signed=signed,
        source_dir=d,
    )


class PluginLoader:
    """Discovers and instantiates plugins from one or more directories.

    The loader is stateless across calls; each :meth:`discover` returns a
    fresh list. Use :class:`PluginRegistry` for the persistent
    enable/disable state.
    """

    def __init__(self, *, search_paths: list[Path] | None = None) -> None:
        if search_paths is None:
            search_paths = [builtin_plugins_dir(), user_plugins_dir()]
        self._search_paths: tuple[Path, ...] = tuple(search_paths)

    @property
    def search_paths(self) -> tuple[Path, ...]:
        return self._search_paths

    def discover(self) -> list[LoadedPlugin]:
        """Walk every search path, load every manifest.json found.

        On a per-plugin error, the error is logged and that plugin is
        skipped; other plugins still load. The caller can examine the
        log to see what was rejected.
        """
        out: list[LoadedPlugin] = []
        seen_names: set[str] = set()
        for root in self._search_paths:
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                if not (child / "manifest.json").exists():
                    continue
                try:
                    plugin = load_plugin(child)
                except PluginError as exc:
                    log.warning("skipping %s: %s", child, exc)
                    continue

                if plugin.manifest.name in seen_names:
                    log.warning(
                        "duplicate plugin name %r at %s; ignoring later copy",
                        plugin.manifest.name, child,
                    )
                    continue
                seen_names.add(plugin.manifest.name)
                out.append(plugin)
        return out
