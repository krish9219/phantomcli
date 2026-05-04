"""Tests for the v4 executor — :mod:`phantom.engine.executor`."""

from __future__ import annotations

import shutil

import pytest

from phantom.engine.executor import (
    ExecuteBashRequest,
    ExecuteBashResult,
    execute_bash,
)
from phantom.errors import (
    PermissionDeniedError,
    SandboxLaunchError,
    SandboxTimeoutError,
)
from phantom.sandbox.policy import ResourceLimits


unshare_available = pytest.mark.skipif(
    shutil.which("unshare") is None or shutil.which("prlimit") is None,
    reason="no sandbox backend available",
)


@pytest.fixture(autouse=True)
def _isolated_phantom_home(tmp_path, monkeypatch):
    """Per-test isolation: fresh PHANTOM_HOME and a clean selection cache.

    The cache reset matters because earlier tests (e.g. test_select.py)
    can leave fake backends pinned for the running process.
    """
    from phantom.sandbox import clear_cache_for_tests

    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    monkeypatch.delenv("PHANTOM_SANDBOX_TIER", raising=False)
    clear_cache_for_tests()
    yield
    clear_cache_for_tests()


# ─── Happy path ───────────────────────────────────────────────────────────────


@unshare_available
class TestExecuteBashHappyPath:
    def test_echo(self, tmp_path):
        req = ExecuteBashRequest(
            command="echo hello",
            workdir=str(tmp_path),
        )
        res = execute_bash(req)
        assert isinstance(res, ExecuteBashResult)
        assert res.exit_code == 0
        assert res.ok is True
        assert "hello" in res.stdout

    def test_writable_workdir(self, tmp_path):
        req = ExecuteBashRequest(
            command=f"echo data > {tmp_path}/out && cat {tmp_path}/out",
            workdir=str(tmp_path),
        )
        res = execute_bash(req)
        assert res.exit_code == 0
        assert "data" in res.stdout
        assert (tmp_path / "out").read_text().strip() == "data"

    def test_workdir_auto_created(self, tmp_path):
        target = tmp_path / "auto"
        assert not target.exists()
        req = ExecuteBashRequest(
            command="pwd",
            workdir=str(target),
        )
        res = execute_bash(req)
        assert res.exit_code == 0
        assert res.stdout.strip() == str(target)


# ─── Blocklist (defence in depth) ─────────────────────────────────────────────


class TestExecuteBashBlocklist:
    def test_rm_rf_root_blocked(self, tmp_path):
        req = ExecuteBashRequest(
            command="rm -rf /",
            workdir=str(tmp_path),
        )
        with pytest.raises(PermissionDeniedError, match="permanent blocklist"):
            execute_bash(req)

    def test_fork_bomb_blocked(self, tmp_path):
        req = ExecuteBashRequest(
            command=":(){ :|:& };:",
            workdir=str(tmp_path),
        )
        with pytest.raises(PermissionDeniedError):
            execute_bash(req)

    def test_mkfs_blocked(self, tmp_path):
        req = ExecuteBashRequest(
            command="mkfs.ext4 /dev/sda1",
            workdir=str(tmp_path),
        )
        with pytest.raises(PermissionDeniedError):
            execute_bash(req)

    def test_shutdown_blocked(self, tmp_path):
        req = ExecuteBashRequest(
            command="shutdown -h now",
            workdir=str(tmp_path),
        )
        with pytest.raises(PermissionDeniedError):
            execute_bash(req)

    def test_blocklist_case_insensitive(self, tmp_path):
        req = ExecuteBashRequest(
            command="RM -RF /",
            workdir=str(tmp_path),
        )
        with pytest.raises(PermissionDeniedError):
            execute_bash(req)


# ─── Empty / invalid input ────────────────────────────────────────────────────


class TestExecuteBashInvalid:
    def test_empty_command_raises(self, tmp_path):
        req = ExecuteBashRequest(
            command="",
            workdir=str(tmp_path),
        )
        with pytest.raises(SandboxLaunchError, match="command is empty"):
            execute_bash(req)

    def test_whitespace_only_command_raises(self, tmp_path):
        req = ExecuteBashRequest(
            command="   \n\t",
            workdir=str(tmp_path),
        )
        with pytest.raises(SandboxLaunchError):
            execute_bash(req)


# ─── Resource limits ─────────────────────────────────────────────────────────


@unshare_available
class TestExecuteBashLimits:
    def test_timeout_enforced(self, tmp_path):
        req = ExecuteBashRequest(
            command="sleep 30",
            workdir=str(tmp_path),
            limits=ResourceLimits(wall_s=2.0, cpu_s=1.5),
        )
        with pytest.raises(SandboxTimeoutError):
            execute_bash(req)

    def test_nonzero_exit_propagates(self, tmp_path):
        req = ExecuteBashRequest(
            command="exit 9",
            workdir=str(tmp_path),
        )
        res = execute_bash(req)
        assert res.exit_code == 9
        assert res.ok is False


# ─── Result.from_sandbox helper ───────────────────────────────────────────────


class TestExecuteBashResultHelper:
    def test_from_sandbox_round_trip(self):
        from phantom.sandbox.result import SandboxResult
        sr = SandboxResult(
            stdout="hi", stderr="", exit_code=0, wall_s=0.01,
            tier="unshare", truncated=False,
        )
        eb = ExecuteBashResult.from_sandbox(sr)
        assert eb.stdout == "hi"
        assert eb.exit_code == 0
        assert eb.tier == "unshare"
        assert eb.truncated is False
        assert eb.ok is True
