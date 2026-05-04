"""Tests for :mod:`phantom.plugins.manifest`."""

from __future__ import annotations

import json

import pytest

from phantom.errors import PluginError
from phantom.plugins.capability import Capability
from phantom.plugins.manifest import PluginManifest


# ─── PluginManifest.from_dict ─────────────────────────────────────────────────


class TestFromDict:
    def test_minimum_valid(self):
        m = PluginManifest.from_dict({
            "name": "demo",
            "version": "1.0.0",
            "entry_point": "demo.module:DemoPlugin",
        })
        assert m.name == "demo"
        assert m.version == "1.0.0"
        assert m.capabilities == frozenset()

    def test_full_valid(self):
        m = PluginManifest.from_dict({
            "name": "demo",
            "version": "1.2.3-rc1",
            "entry_point": "demo.module:DemoPlugin",
            "description": "A demo plugin.",
            "capabilities": ["network", "executor"],
            "homepage": "https://example.com",
            "author": "Aravind",
            "license": "MIT",
            "extras": {"tags": ["sample"]},
        })
        assert m.capabilities == {Capability.NETWORK, Capability.EXECUTOR}
        assert m.extras == {"tags": ["sample"]}

    def test_unknown_top_level_key_rejected(self):
        with pytest.raises(PluginError, match="unknown manifest keys"):
            PluginManifest.from_dict({
                "name": "demo", "version": "1.0.0",
                "entry_point": "m:C", "unknown_field": True,
            })

    def test_missing_required_fields(self):
        with pytest.raises(PluginError, match="missing required key 'name'"):
            PluginManifest.from_dict({"version": "1.0.0", "entry_point": "m:C"})
        with pytest.raises(PluginError, match="missing required key 'version'"):
            PluginManifest.from_dict({"name": "demo", "entry_point": "m:C"})
        with pytest.raises(PluginError, match="missing required key 'entry_point'"):
            PluginManifest.from_dict({"name": "demo", "version": "1.0.0"})

    @pytest.mark.parametrize("bad_name", [
        "Demo",        # uppercase
        "1demo",       # leading digit
        "demo!",       # special char
        "a",           # too short
        "x" * 100,     # too long
    ])
    def test_invalid_name(self, bad_name):
        with pytest.raises(PluginError, match="manifest.name"):
            PluginManifest.from_dict({
                "name": bad_name, "version": "1.0.0", "entry_point": "m:C",
            })

    @pytest.mark.parametrize("bad_version", ["1.0", "1.x", "abc", "1.0.0.0"])
    def test_invalid_version(self, bad_version):
        with pytest.raises(PluginError, match="version"):
            PluginManifest.from_dict({
                "name": "demo", "version": bad_version, "entry_point": "m:C",
            })

    @pytest.mark.parametrize("bad_entry", [
        "no_colon",
        ":NoModule",
        "module:",
        "1bad:Class",
    ])
    def test_invalid_entry_point(self, bad_entry):
        with pytest.raises(PluginError, match="entry_point"):
            PluginManifest.from_dict({
                "name": "demo", "version": "1.0.0", "entry_point": bad_entry,
            })

    def test_unknown_capability(self):
        with pytest.raises(PluginError, match="unknown capability"):
            PluginManifest.from_dict({
                "name": "demo", "version": "1.0.0",
                "entry_point": "m:C",
                "capabilities": ["network", "fly_to_moon"],
            })

    def test_description_too_long(self):
        with pytest.raises(PluginError, match="description"):
            PluginManifest.from_dict({
                "name": "demo", "version": "1.0.0", "entry_point": "m:C",
                "description": "x" * 281,
            })

    def test_capabilities_must_be_list_of_strings(self):
        with pytest.raises(PluginError, match="capabilities"):
            PluginManifest.from_dict({
                "name": "demo", "version": "1.0.0", "entry_point": "m:C",
                "capabilities": "network",
            })
        with pytest.raises(PluginError, match="capabilities"):
            PluginManifest.from_dict({
                "name": "demo", "version": "1.0.0", "entry_point": "m:C",
                "capabilities": [1, 2],
            })

    def test_signature_validation(self):
        with pytest.raises(PluginError, match="signature"):
            PluginManifest.from_dict({
                "name": "demo", "version": "1.0.0", "entry_point": "m:C",
                "signature": "not-an-object",
            })
        with pytest.raises(PluginError, match="signature"):
            PluginManifest.from_dict({
                "name": "demo", "version": "1.0.0", "entry_point": "m:C",
                "signature": {"public_key": "x"},  # missing 'value'
            })

    def test_root_must_be_object(self):
        with pytest.raises(PluginError, match="must be an object"):
            PluginManifest.from_dict([1, 2, 3])  # type: ignore[arg-type]


class TestLoadFromFile:
    def test_load_valid_file(self, tmp_path):
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps({
            "name": "demo", "version": "1.0.0", "entry_point": "m:C",
        }))
        m = PluginManifest.load(p)
        assert m.name == "demo"

    def test_missing_file(self, tmp_path):
        with pytest.raises(PluginError, match="not found"):
            PluginManifest.load(tmp_path / "nope.json")

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "manifest.json"
        p.write_text("{ not valid json")
        with pytest.raises(PluginError, match="not valid JSON"):
            PluginManifest.load(p)


class TestToDict:
    def test_round_trip_minimal(self):
        m = PluginManifest.from_dict({
            "name": "demo", "version": "1.0.0", "entry_point": "m:C",
        })
        d = m.to_dict()
        assert d == {"name": "demo", "version": "1.0.0", "entry_point": "m:C"}

    def test_round_trip_full(self):
        original = {
            "name": "demo",
            "version": "1.0.0",
            "entry_point": "m:C",
            "description": "x",
            "capabilities": ["executor", "network"],
            "homepage": "https://example.com",
            "author": "A",
            "license": "MIT",
            "extras": {"k": "v"},
        }
        m = PluginManifest.from_dict(original)
        d = m.to_dict()
        # Capabilities are sorted by value.
        assert d["capabilities"] == ["executor", "network"]
        # All other fields round-trip.
        for k in ("name", "version", "entry_point", "description",
                  "homepage", "author", "license", "extras"):
            assert d[k] == original[k]
