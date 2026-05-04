"""Tests for the `phantom dashboard` CLI subcommand."""

from __future__ import annotations

from typer.testing import CliRunner

from phantom.cli import _build_dashboard_config, app


class TestDashboardConfigBuilder:
    def test_no_provider_returns_default_config(self):
        cfg = _build_dashboard_config(base_url="", api_key="", model="")
        # Default config has no plugin or memory provider; the echo
        # session_factory remains.
        assert cfg.plugin_provider is None
        assert cfg.memory_provider is None

    def test_with_provider_wires_session_and_plugins(self):
        cfg = _build_dashboard_config(
            base_url="https://api.example.com/v1",
            api_key="sk-x",
            model="gpt-test",
        )
        assert cfg.plugin_provider is not None
        # Calling the plugin_provider produces a real list of plugins.
        plugins = cfg.plugin_provider()
        names = {p["name"] for p in plugins}
        # Builtin plugins are always discoverable.
        assert {"clock", "weather", "todo"} <= names


class TestDashboardSubcommand:
    def test_non_loopback_refused_by_default(self):
        runner = CliRunner()
        result = runner.invoke(
            app, ["dashboard", "--host", "0.0.0.0", "--port", "8000"]
        )
        assert result.exit_code == 2
        assert "non_loopback" in result.output.lower() or "consent" in result.output.lower()

    def test_non_loopback_allowed_via_env(self, monkeypatch):
        # We do NOT actually start uvicorn here; we just verify the
        # command accepts the flag when the env var is set. To avoid
        # a real bind, replace uvicorn.run via monkeypatch.
        runner = CliRunner()
        monkeypatch.setenv("PHANTOM_DASHBOARD_ALLOW_NON_LOOPBACK", "1")
        called: list = []
        import uvicorn  # noqa: F401  (import for monkeypatching)
        monkeypatch.setattr("uvicorn.run", lambda *a, **kw: called.append((a, kw)))
        result = runner.invoke(
            app, ["dashboard", "--host", "0.0.0.0", "--port", "8001"]
        )
        # Should NOT exit 2 (refusal); uvicorn.run was called.
        assert result.exit_code == 0
        assert len(called) == 1

    def test_loopback_default_starts(self, monkeypatch):
        runner = CliRunner()
        called: list = []
        monkeypatch.setattr("uvicorn.run", lambda *a, **kw: called.append((a, kw)))
        result = runner.invoke(app, ["dashboard"])
        assert result.exit_code == 0
        # uvicorn.run was called with host=127.0.0.1
        _, kwargs = called[0]
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 8000
