"""Tests for ``phantom plugin {list,enable,disable}`` CLI."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from phantom.cli import app


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


@pytest.fixture
def runner():
    return CliRunner()


class TestPluginList:
    def test_lists_builtin_plugins(self, runner):
        result = runner.invoke(app, ["plugin", "list"])
        assert result.exit_code == 0
        for name in ("clock", "weather", "gh-search", "code-search", "todo"):
            assert name in result.output

    def test_header_present(self, runner):
        result = runner.invoke(app, ["plugin", "list"])
        assert "NAME" in result.output
        assert "VERSION" in result.output
        assert "ENABLED" in result.output
        assert "CAPABILITIES" in result.output


class TestEnableDisable:
    def test_enable_then_list_shows_enabled(self, runner):
        runner.invoke(app, ["plugin", "enable", "weather"])
        result = runner.invoke(app, ["plugin", "list"])
        assert "weather" in result.output
        # find 'weather' line and confirm 'yes' appears in the same line
        weather_line = next(
            line for line in result.output.splitlines() if "weather " in line
        )
        assert "yes" in weather_line

    def test_disable_then_list_shows_disabled(self, runner):
        runner.invoke(app, ["plugin", "disable", "weather"])
        result = runner.invoke(app, ["plugin", "list"])
        weather_line = next(
            line for line in result.output.splitlines() if "weather " in line
        )
        assert "no" in weather_line

    def test_enable_acks(self, runner):
        result = runner.invoke(app, ["plugin", "enable", "weather"])
        assert result.exit_code == 0
        assert "enabled" in result.output

    def test_disable_acks(self, runner):
        result = runner.invoke(app, ["plugin", "disable", "weather"])
        assert result.exit_code == 0
        assert "disabled" in result.output
