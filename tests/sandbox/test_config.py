"""Tests for :mod:`phantom.config`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.config import Config, SandboxConfig, default_config_path
from phantom.errors import ConfigError


# ─── default_config_path ──────────────────────────────────────────────────────


class TestDefaultConfigPath:
    def test_uses_phantom_home_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        p = default_config_path()
        assert p == tmp_path / "config.json"

    def test_falls_back_to_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PHANTOM_HOME", raising=False)
        # Path.home() reads HOME on POSIX and USERPROFILE on Windows.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        p = default_config_path()
        assert p == tmp_path / ".phantom" / "config.json"


# ─── Config.load ──────────────────────────────────────────────────────────────


class TestConfigLoad:
    def test_default_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        cfg = Config.load()
        assert cfg.sandbox.preferred is None
        assert cfg.sandbox.disabled == ()
        assert cfg.sandbox.audit_log_path is None

    def test_loads_from_explicit_path(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text(json.dumps({
            "sandbox": {
                "preferred": "bwrap",
                "disabled": ["docker"],
                "audit_log_path": "/var/log/phantom/audit.log",
            }
        }))
        cfg = Config.load(cfg_file)
        assert cfg.sandbox.preferred == "bwrap"
        assert cfg.sandbox.disabled == ("docker",)
        assert cfg.sandbox.audit_log_path == "/var/log/phantom/audit.log"

    def test_partial_config(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text(json.dumps({"sandbox": {"preferred": "unshare"}}))
        cfg = Config.load(cfg_file)
        assert cfg.sandbox.preferred == "unshare"
        assert cfg.sandbox.disabled == ()  # default

    def test_empty_object(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text("{}")
        cfg = Config.load(cfg_file)
        assert cfg.sandbox.preferred is None

    def test_malformed_json_raises(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text("{this is not json")
        with pytest.raises(ConfigError, match="not valid JSON"):
            Config.load(cfg_file)

    def test_root_must_be_object(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text("[1,2,3]")
        with pytest.raises(ConfigError, match="root must be a JSON object"):
            Config.load(cfg_file)

    def test_sandbox_must_be_object(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text(json.dumps({"sandbox": "bwrap"}))
        with pytest.raises(ConfigError, match="sandbox must be an object"):
            Config.load(cfg_file)

    def test_preferred_must_be_string_or_null(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text(json.dumps({"sandbox": {"preferred": 42}}))
        with pytest.raises(ConfigError, match="sandbox.preferred must be a string"):
            Config.load(cfg_file)

    def test_disabled_must_be_list_of_strings(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text(json.dumps({"sandbox": {"disabled": "docker"}}))
        with pytest.raises(ConfigError, match="sandbox.disabled must be a list"):
            Config.load(cfg_file)
        cfg_file.write_text(json.dumps({"sandbox": {"disabled": [1, 2]}}))
        with pytest.raises(ConfigError, match="sandbox.disabled must be a list"):
            Config.load(cfg_file)

    def test_audit_path_must_be_string_or_null(self, tmp_path):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text(json.dumps({"sandbox": {"audit_log_path": 42}}))
        with pytest.raises(ConfigError, match="audit_log_path must be a string"):
            Config.load(cfg_file)


# ─── env-var overrides ────────────────────────────────────────────────────────


class TestConfigEnvOverrides:
    def test_phantom_sandbox_tier_env_overrides_preferred(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text(json.dumps({"sandbox": {"preferred": "unshare"}}))
        monkeypatch.setenv("PHANTOM_SANDBOX_TIER", "bwrap")
        cfg = Config.load(cfg_file)
        assert cfg.sandbox.preferred == "bwrap"

    def test_empty_env_var_treated_as_unset(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "phantom.json"
        cfg_file.write_text(json.dumps({"sandbox": {"preferred": "unshare"}}))
        monkeypatch.setenv("PHANTOM_SANDBOX_TIER", "")
        cfg = Config.load(cfg_file)
        assert cfg.sandbox.preferred == "unshare"

    def test_env_with_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_SANDBOX_TIER", "docker")
        cfg = Config.load()
        assert cfg.sandbox.preferred == "docker"
