"""Tests for ``phantom doctor`` CLI."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from phantom.cli import app
from phantom.cli.doctor import build_report
from phantom.sandbox.select import clear_cache


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    monkeypatch.delenv("PHANTOM_SANDBOX_TIER", raising=False)
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def runner():
    return CliRunner()


# ─── build_report (unit) ──────────────────────────────────────────────────────


class TestBuildReport:
    def test_keys(self):
        rep = build_report()
        assert set(rep) == {
            "phantom_version",
            "python_version",
            "platform",
            "packages",
            "backends",
            "selected",
        }

    def test_packages_includes_both(self):
        rep = build_report()
        assert rep["packages"]["phantom"] is True
        assert rep["packages"]["omnicli"] is True

    def test_backends_have_expected_shape(self):
        rep = build_report()
        for b in rep["backends"]:
            assert set(b) == {"name", "tier", "available"}
            assert isinstance(b["name"], str)
            assert isinstance(b["tier"], int)
            assert isinstance(b["available"], bool)

    def test_backends_includes_all_four(self):
        rep = build_report()
        names = {b["name"] for b in rep["backends"]}
        assert names == {"bwrap", "firejail", "unshare", "docker"}

    def test_selected_is_a_known_name_or_none(self):
        rep = build_report()
        if rep["selected"] is not None:
            names = {b["name"] for b in rep["backends"]}
            assert rep["selected"] in names


# ─── CLI: text output ─────────────────────────────────────────────────────────


class TestDoctorTextOutput:
    def test_runs_successfully_when_a_backend_is_available(self, runner):
        result = runner.invoke(app, ["doctor"])
        # Exit 0 if a backend is available, 1 otherwise. On any modern
        # Linux test box we should have at least unshare.
        assert result.exit_code in (0, 1)
        assert "Phantom doctor" in result.output
        assert "Sandbox backends:" in result.output

    def test_lists_all_four_tiers(self, runner):
        result = runner.invoke(app, ["doctor"])
        for name in ("bwrap", "firejail", "unshare", "docker"):
            assert name in result.output

    def test_shows_selected_when_available(self, runner):
        result = runner.invoke(app, ["doctor"])
        if result.exit_code == 0:
            assert "Selected sandbox" in result.output


# ─── CLI: JSON output ─────────────────────────────────────────────────────────


class TestDoctorJsonOutput:
    def test_emits_valid_json(self, runner):
        result = runner.invoke(app, ["doctor", "--json"])
        # Even when a backend is missing (exit 1) the JSON body is on stdout.
        parsed = json.loads(result.output)
        assert "phantom_version" in parsed
        assert "backends" in parsed
        assert isinstance(parsed["backends"], list)


# ─── version subcommand ───────────────────────────────────────────────────────


class TestVersionSubcommand:
    def test_version_subcommand_prints_version(self, runner):
        from phantom._version import __version__
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.output
