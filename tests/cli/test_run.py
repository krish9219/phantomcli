"""Tests for ``phantom run`` CLI."""

from __future__ import annotations

import shutil

import pytest
from typer.testing import CliRunner

from phantom.cli import app
from phantom.sandbox.select import clear_cache


unshare_available = pytest.mark.skipif(
    shutil.which("unshare") is None or shutil.which("prlimit") is None,
    reason="no sandbox backend",
)


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


@unshare_available
class TestPhantomRun:
    def test_echo(self, runner, tmp_path):
        result = runner.invoke(
            app, ["run", "--workdir", str(tmp_path), "--", "echo", "hi"]
        )
        assert result.exit_code == 0
        assert "hi" in result.output

    def test_propagates_nonzero_exit(self, runner, tmp_path):
        result = runner.invoke(
            app, ["run", "--workdir", str(tmp_path), "--", "sh", "-c", "exit 7"]
        )
        assert result.exit_code == 7

    def test_blocklist_blocks(self, runner, tmp_path):
        result = runner.invoke(
            app, ["run", "--workdir", str(tmp_path), "--", "rm", "-rf", "/"]
        )
        # Exit code 126 = blocked by policy.
        assert result.exit_code == 126
        # ``result.stderr`` only exists if the runner was constructed with
        # ``mix_stderr=False``; on click ≥ 8.2 the default merges streams
        # and asking for stderr alone raises. Probe defensively so the
        # test works against both old and new click.
        try:
            stderr_text = result.stderr.lower()
        except (ValueError, AttributeError):
            stderr_text = ""
        assert "blocked" in stderr_text or "blocked" in result.output.lower()

    def test_timeout_exits_124(self, runner, tmp_path):
        # Generous deadline + long sleep so this test does not flake
        # under CI load. See test_run_contract.py for rationale.
        result = runner.invoke(
            app,
            [
                "run",
                "--workdir", str(tmp_path),
                "--wall-s", "2.0",
                "--cpu-s", "1.5",
                "--", "sleep", "30",
            ],
        )
        # 124 is the conventional timeout exit code (matches GNU timeout).
        assert result.exit_code == 124

    def test_no_args_returns_2(self, runner):
        # Typer's argument validation. Empty positional list is invalid.
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 2
