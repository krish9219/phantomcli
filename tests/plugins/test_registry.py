"""Tests for :mod:`phantom.plugins.registry`."""

from __future__ import annotations

import json
import stat

import pytest

from phantom.errors import PluginError
from phantom.plugins.registry import PluginRegistry


class TestRegistryLoad:
    def test_empty_when_file_missing(self, tmp_path):
        r = PluginRegistry.load(tmp_path / "reg.json")
        assert r.known() == []

    def test_loads_existing_file(self, tmp_path):
        p = tmp_path / "reg.json"
        p.write_text(json.dumps({"weather": True, "gh-search": False}))
        r = PluginRegistry.load(p)
        assert r.is_enabled("weather") is True
        assert r.is_enabled("gh-search") is False

    def test_corrupt_json_raises(self, tmp_path):
        p = tmp_path / "reg.json"
        p.write_text("{ garbage }")
        with pytest.raises(PluginError, match="corrupted"):
            PluginRegistry.load(p)

    def test_non_object_root_raises(self, tmp_path):
        p = tmp_path / "reg.json"
        p.write_text("[1,2,3]")
        with pytest.raises(PluginError, match="must be a JSON object"):
            PluginRegistry.load(p)

    def test_unknown_plugin_default_enabled(self, tmp_path):
        r = PluginRegistry.load(tmp_path / "reg.json")
        assert r.is_enabled("never-seen") is True

    def test_strange_values_coerced(self, tmp_path):
        p = tmp_path / "reg.json"
        # Operator hand-edited; we coerce truthiness to bool.
        p.write_text(json.dumps({"a": 1, "b": 0, "c": "yes", "d": ""}))
        r = PluginRegistry.load(p)
        assert r.is_enabled("a") is True
        assert r.is_enabled("b") is False
        assert r.is_enabled("c") is True
        assert r.is_enabled("d") is False


class TestRegistryMutations:
    def test_enable_persists(self, tmp_path):
        p = tmp_path / "reg.json"
        r = PluginRegistry.load(p)
        r.enable("weather")
        # Re-load: state survives.
        r2 = PluginRegistry.load(p)
        assert r2.is_enabled("weather") is True

    def test_disable_persists(self, tmp_path):
        p = tmp_path / "reg.json"
        r = PluginRegistry.load(p)
        r.disable("weather")
        r2 = PluginRegistry.load(p)
        assert r2.is_enabled("weather") is False

    def test_forget_persists(self, tmp_path):
        p = tmp_path / "reg.json"
        r = PluginRegistry.load(p)
        r.disable("weather")
        r.forget("weather")
        r2 = PluginRegistry.load(p)
        assert "weather" not in r2.known()
        # Forgotten = default = True.
        assert r2.is_enabled("weather") is True

    def test_file_mode_0600(self, tmp_path):
        p = tmp_path / "reg.json"
        r = PluginRegistry.load(p)
        r.enable("x")
        assert stat.S_IMODE(p.stat().st_mode) == 0o600

    def test_known_returns_sorted(self, tmp_path):
        r = PluginRegistry.load(tmp_path / "reg.json")
        r.disable("zebra")
        r.enable("alpha")
        assert r.known() == ["alpha", "zebra"]
