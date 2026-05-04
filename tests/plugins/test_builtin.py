"""Tests for the five reference plugins."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from phantom.errors import PluginError
from phantom.plugins.builtin.clock import ClockPlugin
from phantom.plugins.builtin.code_search import CodeSearchPlugin
from phantom.plugins.builtin.gh_search import GhSearchPlugin
from phantom.plugins.builtin.todo import TodoPlugin
from phantom.plugins.builtin.weather import WeatherPlugin
from phantom.plugins.capability import Capability
from phantom.plugins.manifest import PluginManifest
from phantom.plugins.plugin import PluginContext
from phantom.sandbox import SandboxPolicy


def _ctx(workdir: Path, caps: frozenset[Capability], extras: dict | None = None) -> PluginContext:
    return PluginContext(
        workdir=workdir,
        sandbox_policy=SandboxPolicy(
            workdir=str(workdir), writable_paths=(str(workdir),),
        ),
        capabilities=caps,
        manifest=PluginManifest.from_dict({
            "name": "demo", "version": "1.0.0", "entry_point": "x:Y",
        }),
        extras=extras or {},
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


# ─── clock ───────────────────────────────────────────────────────────────────


class TestClockPlugin:
    def test_returns_iso_8601(self, tmp_path):
        p = ClockPlugin(manifest=PluginManifest.from_dict({
            "name": "clock", "version": "1.0.0", "entry_point": "x:Y",
        }))
        out = p.call(_ctx(tmp_path, frozenset()), {})
        assert "now" in out
        # 2026-04-25T17:30:01.123456Z format.
        assert out["now"].endswith("Z")
        assert "T" in out["now"]


# ─── weather ─────────────────────────────────────────────────────────────────


class _FakeHttp:
    def __init__(self, payload):
        self._payload = payload
        self.last_url = None

    def get(self, url):
        self.last_url = url
        class _Resp:
            def __init__(s, p): s._p = p
            def json(s): return s._p
        return _Resp(self._payload)


class TestWeatherPlugin:
    def test_happy_path(self, tmp_path):
        fake_http = _FakeHttp({
            "current_weather": {
                "temperature": 21.4, "windspeed": 12.5, "weathercode": 1,
            }
        })
        p = WeatherPlugin(manifest=PluginManifest.from_dict({
            "name": "weather", "version": "1.0.0",
            "entry_point": "x:Y", "capabilities": ["network"],
        }))
        ctx = _ctx(tmp_path, frozenset({Capability.NETWORK}), extras={"http": fake_http})
        out = p.call(ctx, {"lat": 51.5, "lon": -0.12})
        assert out == {"temperature_c": 21.4, "windspeed_kmh": 12.5, "code": 1}
        assert "latitude=51.5" in fake_http.last_url
        assert "longitude=-0.12" in fake_http.last_url

    def test_requires_network_capability(self, tmp_path):
        p = WeatherPlugin(manifest=PluginManifest.from_dict({
            "name": "weather", "version": "1.0.0",
            "entry_point": "x:Y", "capabilities": ["network"],
        }))
        ctx = _ctx(tmp_path, frozenset())  # no caps granted
        with pytest.raises(PluginError, match="network"):
            p.call(ctx, {"lat": 0.0, "lon": 0.0})

    def test_missing_coordinates(self, tmp_path):
        p = WeatherPlugin(manifest=PluginManifest.from_dict({
            "name": "weather", "version": "1.0.0",
            "entry_point": "x:Y", "capabilities": ["network"],
        }))
        ctx = _ctx(tmp_path, frozenset({Capability.NETWORK}), extras={"http": _FakeHttp({})})
        with pytest.raises(PluginError, match="lat.*lon"):
            p.call(ctx, {})

    def test_invalid_timezone_type(self, tmp_path):
        p = WeatherPlugin(manifest=PluginManifest.from_dict({
            "name": "weather", "version": "1.0.0",
            "entry_point": "x:Y", "capabilities": ["network"],
        }))
        ctx = _ctx(tmp_path, frozenset({Capability.NETWORK}), extras={"http": _FakeHttp({})})
        with pytest.raises(PluginError, match="timezone"):
            p.call(ctx, {"lat": 0, "lon": 0, "timezone": 42})


# ─── todo ─────────────────────────────────────────────────────────────────────


class TestTodoPlugin:
    def _new(self, tmp_path):
        return TodoPlugin(manifest=PluginManifest.from_dict({
            "name": "todo", "version": "1.0.0",
            "entry_point": "x:Y", "capabilities": ["memory"],
        }))

    def test_add_and_list(self, tmp_path):
        p = self._new(tmp_path)
        ctx = _ctx(tmp_path, frozenset({Capability.MEMORY}))
        r1 = p.call(ctx, {"action": "add", "text": "buy milk"})
        r2 = p.call(ctx, {"action": "add", "text": "write tests"})
        listed = p.call(ctx, {"action": "list"})
        assert {item["text"] for item in listed["items"]} == {"buy milk", "write tests"}
        assert r1["id"] != r2["id"]

    def test_done(self, tmp_path):
        p = self._new(tmp_path)
        ctx = _ctx(tmp_path, frozenset({Capability.MEMORY}))
        r = p.call(ctx, {"action": "add", "text": "x"})
        out = p.call(ctx, {"action": "done", "id": r["id"]})
        assert out == {"updated": 1}
        listed = p.call(ctx, {"action": "list"})
        assert listed["items"] == []
        full = p.call(ctx, {"action": "list", "include_done": True})
        assert full["items"][0]["done"] is True

    def test_remove(self, tmp_path):
        p = self._new(tmp_path)
        ctx = _ctx(tmp_path, frozenset({Capability.MEMORY}))
        r = p.call(ctx, {"action": "add", "text": "x"})
        out = p.call(ctx, {"action": "remove", "id": r["id"]})
        assert out == {"removed": 1}
        listed = p.call(ctx, {"action": "list"})
        assert listed["items"] == []

    def test_unknown_action(self, tmp_path):
        p = self._new(tmp_path)
        ctx = _ctx(tmp_path, frozenset({Capability.MEMORY}))
        with pytest.raises(PluginError, match="unknown action"):
            p.call(ctx, {"action": "fly"})

    def test_requires_memory_capability(self, tmp_path):
        p = self._new(tmp_path)
        ctx = _ctx(tmp_path, frozenset())  # no caps
        with pytest.raises(PluginError, match="memory"):
            p.call(ctx, {"action": "list"})

    def test_add_text_must_be_non_empty(self, tmp_path):
        p = self._new(tmp_path)
        ctx = _ctx(tmp_path, frozenset({Capability.MEMORY}))
        with pytest.raises(PluginError, match="non-empty"):
            p.call(ctx, {"action": "add", "text": ""})

    def test_done_id_must_be_int(self, tmp_path):
        p = self._new(tmp_path)
        ctx = _ctx(tmp_path, frozenset({Capability.MEMORY}))
        with pytest.raises(PluginError, match="integer"):
            p.call(ctx, {"action": "done", "id": "abc"})


# ─── gh-search and code-search require external tools ────────────────────────


class TestGhSearchPlugin:
    def test_requires_capabilities(self, tmp_path):
        p = GhSearchPlugin(manifest=PluginManifest.from_dict({
            "name": "gh-search", "version": "1.0.0",
            "entry_point": "x:Y",
            "capabilities": ["network", "executor"],
        }))
        # Missing executor.
        ctx = _ctx(tmp_path, frozenset({Capability.NETWORK}))
        with pytest.raises(PluginError, match="capability"):
            p.call(ctx, {"query": "x"})
        # Missing network.
        ctx = _ctx(tmp_path, frozenset({Capability.EXECUTOR}))
        with pytest.raises(PluginError, match="capability"):
            p.call(ctx, {"query": "x"})

    def test_invalid_payload(self, tmp_path):
        p = GhSearchPlugin(manifest=PluginManifest.from_dict({
            "name": "gh-search", "version": "1.0.0",
            "entry_point": "x:Y",
            "capabilities": ["network", "executor"],
        }))
        ctx = _ctx(tmp_path, frozenset({Capability.NETWORK, Capability.EXECUTOR}))
        with pytest.raises(PluginError, match="non-empty"):
            p.call(ctx, {"query": ""})
        with pytest.raises(PluginError, match="type"):
            p.call(ctx, {"query": "x", "type": "fly"})
        with pytest.raises(PluginError, match="limit"):
            p.call(ctx, {"query": "x", "limit": 0})


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
class TestCodeSearchPlugin:
    def test_finds_pattern(self, tmp_path):
        # Create a tiny file to search.
        (tmp_path / "hello.py").write_text("def greet(): return 'hello world'\n")
        p = CodeSearchPlugin(manifest=PluginManifest.from_dict({
            "name": "code-search", "version": "1.0.0",
            "entry_point": "x:Y",
            "capabilities": ["executor", "filesystem"],
        }))
        ctx = _ctx(tmp_path, frozenset({Capability.EXECUTOR, Capability.FILESYSTEM}))
        out = p.call(ctx, {"pattern": "greet", "path": str(tmp_path)})
        assert any("greet" in m["text"] for m in out["matches"])

    def test_path_must_exist(self, tmp_path):
        p = CodeSearchPlugin(manifest=PluginManifest.from_dict({
            "name": "code-search", "version": "1.0.0",
            "entry_point": "x:Y",
            "capabilities": ["executor", "filesystem"],
        }))
        ctx = _ctx(tmp_path, frozenset({Capability.EXECUTOR, Capability.FILESYSTEM}))
        with pytest.raises(PluginError, match="does not exist"):
            p.call(ctx, {"pattern": "x", "path": "/no/such/path"})


class TestCodeSearchValidation:
    """Validation that runs without rg installed."""

    def test_requires_executor(self, tmp_path):
        p = CodeSearchPlugin(manifest=PluginManifest.from_dict({
            "name": "code-search", "version": "1.0.0",
            "entry_point": "x:Y",
            "capabilities": ["executor"],
        }))
        ctx = _ctx(tmp_path, frozenset())
        with pytest.raises(PluginError, match="executor"):
            p.call(ctx, {"pattern": "x", "path": str(tmp_path)})

    def test_pattern_required(self, tmp_path):
        p = CodeSearchPlugin(manifest=PluginManifest.from_dict({
            "name": "code-search", "version": "1.0.0",
            "entry_point": "x:Y", "capabilities": ["executor"],
        }))
        ctx = _ctx(tmp_path, frozenset({Capability.EXECUTOR}))
        with pytest.raises(PluginError, match="pattern"):
            p.call(ctx, {"path": str(tmp_path)})

    def test_path_must_be_absolute(self, tmp_path):
        p = CodeSearchPlugin(manifest=PluginManifest.from_dict({
            "name": "code-search", "version": "1.0.0",
            "entry_point": "x:Y", "capabilities": ["executor"],
        }))
        ctx = _ctx(tmp_path, frozenset({Capability.EXECUTOR}))
        with pytest.raises(PluginError, match="absolute"):
            p.call(ctx, {"pattern": "x", "path": "relative/path"})
