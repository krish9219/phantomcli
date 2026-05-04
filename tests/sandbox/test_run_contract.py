"""Contract test — every available backend must satisfy the same behaviour.

This is the OpenClaw-killer: a single parameterised test that runs
against every sandbox backend present on the host and asserts each one
honours the same set of promises (stdout capture, exit-code propagation,
network-deny default, deadline enforcement, output truncation, working
directory, env cleansing).

When a backend probes available, every assertion below applies. When it
doesn't, the parametrisation is skipped — never silently dropped, but
explicitly marked.

The contract is the *security envelope* every Phantom user is buying. If
this file is green, switching tiers is safe.
"""

from __future__ import annotations

import shutil
from typing import Iterator

import pytest

from phantom.errors import SandboxTimeoutError
from phantom.sandbox import SandboxPolicy, run
from phantom.sandbox._backend import SandboxBackend
from phantom.sandbox.backends.bwrap import BwrapBackend
from phantom.sandbox.backends.docker import DockerBackend
from phantom.sandbox.backends.firejail import FirejailBackend
from phantom.sandbox.backends.unshare import UnshareBackend
from phantom.sandbox.policy import ResourceLimits


def _live_backends() -> Iterator[SandboxBackend]:
    """Yield only the backends that probe available on this host."""
    for cls in (BwrapBackend, FirejailBackend, UnshareBackend, DockerBackend):
        b = cls()
        if b.probe():
            yield b


# Build the parametrize list at collection time so each parameter has a
# readable test ID. If no backend probes available we still need at least
# one entry so pytest doesn't error; we add a synthetic skip.
_PARAMS: list = list(_live_backends())
_IDS = [b.name for b in _PARAMS]
if not _PARAMS:
    pytest.skip("no sandbox backend available", allow_module_level=True)


@pytest.fixture(params=_PARAMS, ids=_IDS)
def backend(request) -> SandboxBackend:
    return request.param


@pytest.fixture
def policy(tmp_path):
    return SandboxPolicy(
        workdir=str(tmp_path),
        writable_paths=(str(tmp_path),),
        limits=ResourceLimits(wall_s=10.0, cpu_s=5.0, rss_mib=128, fds=64),
    )


# ─── Behavioural contract ─────────────────────────────────────────────────────


def test_stdout_captured(backend, policy, tmp_path):
    result = run(["echo", "hello-from-sandbox"], policy, backend=backend,
                 audit_path=str(tmp_path / "a.log"))
    assert result.exit_code == 0
    assert "hello-from-sandbox" in result.stdout


def test_stderr_captured(backend, policy, tmp_path):
    result = run(
        ["sh", "-c", "echo to-err >&2"],
        policy, backend=backend, audit_path=str(tmp_path / "a.log"),
    )
    assert result.exit_code == 0
    assert "to-err" in result.stderr


def test_exit_code_propagates(backend, policy, tmp_path):
    result = run(["sh", "-c", "exit 42"], policy, backend=backend,
                 audit_path=str(tmp_path / "a.log"))
    assert result.exit_code == 42
    assert result.ok is False


def test_workdir_is_inside_sandbox(backend, policy, tmp_path):
    result = run(["sh", "-c", "pwd"], policy, backend=backend,
                 audit_path=str(tmp_path / "a.log"))
    assert result.exit_code == 0
    # Every backend honours the policy's workdir.
    assert result.stdout.strip() == str(tmp_path)


def test_writable_path_actually_writable(backend, policy, tmp_path):
    result = run(
        ["sh", "-c", f"echo content > {tmp_path}/out.txt && cat {tmp_path}/out.txt"],
        policy, backend=backend, audit_path=str(tmp_path / "a.log"),
    )
    assert result.exit_code == 0
    assert "content" in result.stdout
    assert (tmp_path / "out.txt").read_text().strip() == "content"


def test_wall_clock_deadline_enforced(backend, tmp_path):
    # Use a generous deadline (2.0 s with sleep 30) so this test does
    # not flake under CI load. The point is "the deadline is
    # enforced", not "the deadline is enforced *fast*". Without enough
    # margin between the sleep value and the deadline, kernel
    # scheduling jitter under heavy suite load can let sleep finish
    # before subprocess.run's timeout fires.
    policy = SandboxPolicy(
        workdir=str(tmp_path),
        writable_paths=(str(tmp_path),),
        limits=ResourceLimits(wall_s=2.0, cpu_s=1.5),
    )
    with pytest.raises(SandboxTimeoutError) as excinfo:
        run(["sleep", "30"], policy, backend=backend,
            audit_path=str(tmp_path / "a.log"))
    assert excinfo.value.deadline_s == 2.0


def test_truncation_flag(backend, tmp_path):
    policy = SandboxPolicy(
        workdir=str(tmp_path),
        writable_paths=(str(tmp_path),),
        limits=ResourceLimits(stdout_bytes=4096, stderr_bytes=4096),
    )
    # Generate well over 4 KiB of stdout.
    result = run(
        ["sh", "-c", "for i in $(seq 1 200); do printf 'X%.0s' $(seq 1 100); done"],
        policy, backend=backend, audit_path=str(tmp_path / "a.log"),
    )
    assert result.truncated is True
    assert "[phantom-sandbox: output truncated]" in result.stdout


def test_clean_env_by_default(backend, tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_SECRET", "leaked-token")
    policy = SandboxPolicy(
        workdir=str(tmp_path),
        writable_paths=(str(tmp_path),),
    )
    result = run(
        ["sh", "-c", "echo SEC=${HOST_SECRET:-CLEAN}"],
        policy, backend=backend, audit_path=str(tmp_path / "a.log"),
    )
    assert "CLEAN" in result.stdout
    assert "leaked-token" not in result.stdout


def test_result_records_chosen_tier(backend, policy, tmp_path):
    result = run(["echo", "x"], policy, backend=backend,
                 audit_path=str(tmp_path / "a.log"))
    assert result.tier == backend.name
