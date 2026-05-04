"""Tests for the layered settings hierarchy."""
from __future__ import annotations

import json
import os

import pytest

from omnicli.settings_layers import (
    load, get, get_source, is_locked,
    SYSTEM, USER, PROJECT_SHARED, PROJECT_LOCAL,
)


@pytest.fixture
def layered(tmp_path, monkeypatch):
    """Build four layer files under tmp_path; return a helper that writes
    each layer + returns the project root (for `start=`)."""
    sys_path  = tmp_path / "system.json"
    user_path = tmp_path / "user.json"
    proj_dir  = tmp_path / "myproj" / ".phantom"
    proj_dir.mkdir(parents=True)
    proj_shared = proj_dir / "settings.json"
    proj_local  = proj_dir / "settings.local.json"

    monkeypatch.setenv("PHANTOM_SYSTEM_SETTINGS", str(sys_path))
    monkeypatch.setenv("PHANTOM_USER_SETTINGS",   str(user_path))

    def _write(layer: str, data: dict):
        mapping = {
            "system":  sys_path,
            "user":    user_path,
            "project_shared": proj_shared,
            "project_local":  proj_local,
        }
        mapping[layer].write_text(json.dumps(data))

    return {
        "write":  _write,
        "root":   str(tmp_path / "myproj"),
        "paths": {
            "system": sys_path, "user": user_path,
            "project_shared": proj_shared, "project_local": proj_local,
        },
    }


class TestEmptyHierarchy:
    def test_empty_returns_default(self, layered):
        s = load(start=layered["root"])
        assert s.get("missing_key", "fallback") == "fallback"
        assert s.source("missing_key") == "(default)"


class TestSingleLayer:
    def test_system_only(self, layered):
        layered["write"]("system", {"model": "claude-opus"})
        s = load(start=layered["root"])
        assert s.get("model") == "claude-opus"
        assert s.source("model") == SYSTEM

    def test_user_only(self, layered):
        layered["write"]("user", {"theme": "dark"})
        s = load(start=layered["root"])
        assert s.get("theme") == "dark"
        assert s.source("theme") == USER

    def test_project_shared_only(self, layered):
        layered["write"]("project_shared", {"lint": "strict"})
        s = load(start=layered["root"])
        assert s.get("lint") == "strict"
        assert s.source("lint") == PROJECT_SHARED

    def test_project_local_only(self, layered):
        layered["write"]("project_local", {"debug": True})
        s = load(start=layered["root"])
        assert s.get("debug") is True
        assert s.source("debug") == PROJECT_LOCAL


class TestPriorityOrdering:
    def test_user_overrides_system(self, layered):
        layered["write"]("system", {"model": "A"})
        layered["write"]("user",   {"model": "B"})
        s = load(start=layered["root"])
        assert s.get("model") == "B"
        assert s.source("model") == USER

    def test_project_shared_overrides_user(self, layered):
        layered["write"]("user",           {"model": "B"})
        layered["write"]("project_shared", {"model": "C"})
        s = load(start=layered["root"])
        assert s.get("model") == "C"
        assert s.source("model") == PROJECT_SHARED

    def test_project_local_beats_all(self, layered):
        layered["write"]("system",         {"model": "A"})
        layered["write"]("user",           {"model": "B"})
        layered["write"]("project_shared", {"model": "C"})
        layered["write"]("project_local",  {"model": "D"})
        s = load(start=layered["root"])
        assert s.get("model") == "D"
        assert s.source("model") == PROJECT_LOCAL

    def test_merge_preserves_unique_keys_across_layers(self, layered):
        layered["write"]("system",         {"a": 1, "b": 2})
        layered["write"]("project_local",  {"c": 3, "b": 22})
        s = load(start=layered["root"])
        assert s.get("a") == 1   # only in system
        assert s.get("b") == 22  # overridden by local
        assert s.get("c") == 3   # only in local


class TestLockedKeys:
    def test_locked_key_in_system_blocks_override(self, layered):
        layered["write"]("system", {
            "model": "enforced-model",
            "locked_keys": ["model"],
        })
        layered["write"]("user", {"model": "user-choice"})
        s = load(start=layered["root"])
        assert s.get("model") == "enforced-model"
        assert s.source("model") == SYSTEM
        assert s.is_locked("model") is True

    def test_unlocked_key_still_overridable(self, layered):
        layered["write"]("system", {
            "model": "enforced",
            "theme": "enforced-theme",
            "locked_keys": ["model"],
        })
        layered["write"]("user", {"model": "override", "theme": "override-theme"})
        s = load(start=layered["root"])
        assert s.get("model") == "enforced"
        assert s.get("theme") == "override-theme"

    def test_locked_keys_itself_not_exposed(self, layered):
        layered["write"]("system", {"a": 1, "locked_keys": ["a"]})
        s = load(start=layered["root"])
        assert "locked_keys" not in s.values

    def test_malformed_locked_keys_ignored(self, layered):
        layered["write"]("system", {"model": "A", "locked_keys": "not-a-list"})
        layered["write"]("user",   {"model": "B"})
        s = load(start=layered["root"])
        # Malformed locked_keys ignored → user override wins
        assert s.get("model") == "B"


class TestMissingLayers:
    def test_missing_system_file_not_an_error(self, layered, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_SYSTEM_SETTINGS", str(tmp_path / "nope.json"))
        s = load(start=layered["root"])
        assert isinstance(s.values, dict)

    def test_broken_json_not_an_error(self, layered):
        # Write garbage to user layer
        layered["paths"]["user"].write_text("{broken json")
        layered["write"]("project_local", {"x": 1})
        s = load(start=layered["root"])
        assert s.get("x") == 1  # project_local still works

    def test_non_object_json_ignored(self, layered):
        layered["paths"]["user"].write_text('["a list"]')
        layered["write"]("project_local", {"x": 1})
        s = load(start=layered["root"])
        assert s.get("x") == 1


class TestApi:
    def test_top_level_get_and_source(self, layered):
        layered["write"]("user", {"foo": "bar"})
        assert get("foo", start=layered["root"]) == "bar"
        assert get_source("foo", start=layered["root"]) == USER

    def test_top_level_is_locked(self, layered):
        layered["write"]("system", {"x": 1, "locked_keys": ["x"]})
        assert is_locked("x", start=layered["root"]) is True
        assert is_locked("other", start=layered["root"]) is False


class TestProjectRootWalking:
    def test_walks_up_to_find_phantom_dir(self, tmp_path, monkeypatch):
        # Build tmp_path/root/.phantom/settings.json and query from deeply nested cwd
        monkeypatch.setenv("PHANTOM_SYSTEM_SETTINGS", str(tmp_path / "no-sys.json"))
        monkeypatch.setenv("PHANTOM_USER_SETTINGS",   str(tmp_path / "no-user.json"))
        root = tmp_path / "root"
        phantom_dir = root / ".phantom"
        phantom_dir.mkdir(parents=True)
        (phantom_dir / "settings.json").write_text('{"deep": "found"}')
        nested = root / "a" / "b" / "c"
        nested.mkdir(parents=True)
        s = load(start=str(nested))
        assert s.get("deep") == "found"
        assert s.source("deep") == PROJECT_SHARED
