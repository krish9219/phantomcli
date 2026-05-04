"""Tests for :mod:`phantom.plugins.loader` against the bundled plugins."""

from __future__ import annotations

import json
import textwrap

import pytest

from phantom.errors import PluginError
from phantom.plugins.capability import Capability
from phantom.plugins.loader import (
    PluginLoader,
    builtin_plugins_dir,
    load_plugin,
    user_plugins_dir,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


# ─── load_plugin (single dir) ─────────────────────────────────────────────────


class TestLoadPlugin:
    def test_loads_clock_plugin(self):
        builtin = builtin_plugins_dir() / "clock"
        loaded = load_plugin(builtin)
        assert loaded.manifest.name == "clock"
        assert not loaded.signed
        assert loaded.instance.manifest.name == "clock"

    def test_missing_manifest_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(PluginError, match="no manifest.json"):
            load_plugin(empty)

    def test_bad_entry_point_module_raises(self, tmp_path):
        d = tmp_path / "p"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "name": "broken", "version": "1.0.0",
            "entry_point": "no.such.module:Plugin",
        }))
        with pytest.raises(PluginError, match="could not be imported"):
            load_plugin(d)

    def test_bad_entry_point_class_raises(self, tmp_path):
        d = tmp_path / "p"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "name": "broken", "version": "1.0.0",
            "entry_point": "phantom.plugins.builtin.clock:NoSuchClass",
        }))
        with pytest.raises(PluginError, match="has no attribute"):
            load_plugin(d)

    def test_entry_point_not_a_plugin_subclass_raises(self, tmp_path):
        # Point at a class that exists but isn't a Plugin.
        d = tmp_path / "p"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "name": "broken", "version": "1.0.0",
            "entry_point": "json:JSONDecoder",
        }))
        with pytest.raises(PluginError, match="must resolve to a Plugin subclass"):
            load_plugin(d)


# ─── PluginLoader.discover ────────────────────────────────────────────────────


class TestPluginLoaderDiscover:
    def test_discovers_all_builtin_plugins(self):
        loader = PluginLoader(search_paths=[builtin_plugins_dir()])
        plugins = loader.discover()
        names = {p.manifest.name for p in plugins}
        # v1.0 added 3 first-party plugins; the original 5 must still be present.
        assert {"clock", "weather", "gh-search", "code-search", "todo"}.issubset(names)
        assert {"github-pr", "web-screenshot", "code-review"}.issubset(names)

    def test_discover_skips_dirs_without_manifest(self, tmp_path):
        (tmp_path / "no-manifest").mkdir()
        loader = PluginLoader(search_paths=[tmp_path])
        assert loader.discover() == []

    def test_discover_skips_files(self, tmp_path):
        (tmp_path / "stray-file.txt").write_text("hi")
        loader = PluginLoader(search_paths=[tmp_path])
        assert loader.discover() == []

    def test_discover_continues_on_per_plugin_error(self, tmp_path, caplog):
        # One good (the clock) and one bad (broken manifest).
        good = tmp_path / "ok"
        good.mkdir()
        (good / "manifest.json").write_text(json.dumps({
            "name": "ok",
            "version": "1.0.0",
            "entry_point": "phantom.plugins.builtin.clock:ClockPlugin",
        }))
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "manifest.json").write_text("{ not valid json")
        loader = PluginLoader(search_paths=[tmp_path])
        with caplog.at_level("WARNING"):
            plugins = loader.discover()
        names = {p.manifest.name for p in plugins}
        assert names == {"ok"}

    def test_default_search_paths_include_builtin_and_user(self):
        loader = PluginLoader()
        # Two paths: builtin first, user second.
        assert loader.search_paths[0] == builtin_plugins_dir()
        assert loader.search_paths[1] == user_plugins_dir()

    def test_discover_dedupes_by_name(self, tmp_path):
        # Two directories with the same plugin name. The first wins.
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        for d in (d1, d2):
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({
                "name": "dup", "version": "1.0.0",
                "entry_point": "phantom.plugins.builtin.clock:ClockPlugin",
            }))
        loader = PluginLoader(search_paths=[tmp_path])
        plugins = loader.discover()
        # First ('a') wins because we iterate sorted.
        assert len(plugins) == 1


# ─── user_plugins_dir / builtin_plugins_dir ───────────────────────────────────


class TestSearchPaths:
    def test_user_plugins_dir_uses_phantom_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / "myhome"))
        p = user_plugins_dir()
        assert p == tmp_path / "myhome" / "plugins"
        assert p.is_dir()

    def test_builtin_plugins_dir_lives_inside_package(self):
        p = builtin_plugins_dir()
        assert p.exists()
        # The five reference plugins live here.
        for name in ("clock", "weather", "gh_search", "code_search", "todo"):
            assert (p / name / "manifest.json").exists()
