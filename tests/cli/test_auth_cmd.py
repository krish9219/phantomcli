"""Tests for `phantom auth {login,logout,status}`."""

from __future__ import annotations

import json
import time

import pytest
from typer.testing import CliRunner

from phantom.agent.oauth_provider import TokenSet, TokenStore
from phantom.cli import app


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


@pytest.fixture
def runner():
    return CliRunner()


# ─── status ──────────────────────────────────────────────────────────────────


class TestStatus:
    def test_no_tokens_shows_all_absent(self, runner):
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0
        for name in ("openai", "anthropic", "google"):
            assert name in result.output
        assert result.output.count("no") >= 3

    def test_status_with_token_shows_present(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
        store = TokenStore.default()
        store.save("anthropic", TokenSet(
            access_token="AT", refresh_token="RT",
            expires_at=time.time() + 3600,
        ))
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0
        # The 'anthropic' row should show present=yes valid=yes.
        anth_line = next(
            line for line in result.output.splitlines() if "anthropic" in line
        )
        assert "yes" in anth_line

    def test_status_json(self, runner):
        result = runner.invoke(app, ["auth", "status", "--json"])
        assert result.exit_code == 0
        rows = json.loads(result.output)
        names = {r["provider"] for r in rows}
        assert names == {"openai", "anthropic", "google", "github"}
        assert all(r["present"] is False for r in rows)


# ─── logout ──────────────────────────────────────────────────────────────────


class TestLogout:
    def test_idempotent_without_token(self, runner):
        result = runner.invoke(app, ["auth", "logout", "--provider", "anthropic"])
        assert result.exit_code == 0
        assert "forgotten" in result.output

    def test_removes_token(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
        store = TokenStore.default()
        store.save("anthropic", TokenSet(access_token="AT"))
        runner.invoke(app, ["auth", "logout", "--provider", "anthropic"])
        assert store.load("anthropic") is None

    def test_unknown_provider_rejected(self, runner):
        result = runner.invoke(app, ["auth", "logout", "--provider", "klingon"])
        assert result.exit_code == 2


# ─── login (driven via fake flow) ────────────────────────────────────────────


class TestLoginPath:
    def test_unknown_provider(self, runner):
        result = runner.invoke(app, ["auth", "login", "--provider", "klingon"])
        assert result.exit_code == 2


# ─── flow factory ───────────────────────────────────────────────────────────


class TestBuildFlow:
    def test_openai(self):
        from phantom.cli.auth import build_flow
        f = build_flow("openai")
        assert "openai.com" in f.token_endpoint

    def test_env_override_picked_up(self, monkeypatch):
        monkeypatch.setenv("PHANTOM_OAUTH_OPENAI_CLIENT_ID", "MY_CID")
        from phantom.cli.auth import build_flow
        f = build_flow("openai")
        assert f.client_id == "MY_CID"

    def test_unknown_raises(self):
        from phantom.cli.auth import build_flow
        from phantom.errors import PhantomError
        with pytest.raises(PhantomError, match="unknown provider"):
            build_flow("klingon")
