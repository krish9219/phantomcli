"""Stage 2 smoke test — asserts the plugin SDK is wired and working.

Per ADR-0006 every stage closes with a smoke test. Stage 2's
deliverables (`docs/stages/STAGE_2.md` § "Smoke test"):

1. The reference plugins are discoverable.
2. The clock plugin returns ISO-8601 time.
3. The weather plugin's manifest declares the network capability.
4. A malformed manifest raises PluginError.
5. The `phantom plugin list` CLI lists all five reference plugins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.errors import PluginError
from phantom.plugins.builtin.clock import ClockPlugin
from phantom.plugins.capability import Capability
from phantom.plugins.loader import (
    PluginLoader,
    builtin_plugins_dir,
    load_plugin,
)
from phantom.plugins.manifest import PluginManifest
from phantom.plugins.plugin import PluginContext
from phantom.sandbox import SandboxPolicy


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


@pytest.mark.stage2
def test_reference_plugins_discoverable():
    loader = PluginLoader(search_paths=[builtin_plugins_dir()])
    plugins = loader.discover()
    names = {p.manifest.name for p in plugins}
    # v1.0 added 3 first-party plugins; the original 5 must still be present.
    assert {"clock", "weather", "gh-search", "code-search", "todo"}.issubset(names)
    assert {"github-pr", "web-screenshot", "code-review"}.issubset(names)


@pytest.mark.stage2
def test_clock_plugin_returns_iso_8601(tmp_path):
    clock = load_plugin(builtin_plugins_dir() / "clock").instance
    ctx = PluginContext(
        workdir=tmp_path,
        sandbox_policy=SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        ),
        capabilities=frozenset(),
        manifest=clock.manifest,
        extras={},
    )
    out = clock.call(ctx, {})
    assert "now" in out and out["now"].endswith("Z") and "T" in out["now"]


@pytest.mark.stage2
def test_weather_manifest_declares_network_capability():
    weather = load_plugin(builtin_plugins_dir() / "weather")
    assert Capability.NETWORK in weather.manifest.capabilities


@pytest.mark.stage2
def test_malformed_manifest_raises(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "name": "BAD", "version": "x", "entry_point": "no",
    }))
    with pytest.raises(PluginError):
        load_plugin(d)


@pytest.mark.stage2
def test_phantom_plugin_list_cli_shows_all_five():
    from typer.testing import CliRunner
    from phantom.cli import app
    result = CliRunner().invoke(app, ["plugin", "list"])
    assert result.exit_code == 0
    for name in ("clock", "weather", "gh-search", "code-search", "todo",
                 "github-pr", "web-screenshot", "code-review"):
        assert name in result.output


@pytest.mark.stage2
def test_phantom_stage_advanced_to_2_or_higher():
    import phantom
    assert phantom.feature_flags()["stage"] >= 2
