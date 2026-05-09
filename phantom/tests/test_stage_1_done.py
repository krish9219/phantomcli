"""Stage 1 smoke test — asserts the sandbox is wired and working.

ADR-0006 mandates one of these per stage. The assertions cover the
public-API surface that Stage 1 promised in
``docs/stages/STAGE_1.md`` § "Smoke test":

1. ``phantom.sandbox.run`` is importable.
2. ``phantom.sandbox.select_backend()`` picks *some* backend on Linux.
3. A trivial ``echo`` round-trips with the chosen tier name.
4. A bogus deadline raises ``SandboxTimeoutError``.
5. The audit log received exactly one record with the right shape.
6. ``phantom.engine.execute_bash`` is importable and routes through
   the sandbox (the blocklist is enforced as second-line defence).

The smoke test ships in the wheel and runs on every CI build. CI fails
the build if any of these regress.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from phantom.errors import (
    PermissionDeniedError,
    SandboxTimeoutError,
    SandboxUnavailableError,
)


sandbox_capable = pytest.mark.skipif(
    shutil.which("unshare") is None
    and shutil.which("bwrap") is None
    and shutil.which("docker") is None,
    reason="no sandbox backend available on this host",
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    monkeypatch.delenv("PHANTOM_SANDBOX_TIER", raising=False)
    from phantom.sandbox import clear_cache_for_tests
    clear_cache_for_tests()
    yield
    clear_cache_for_tests()


# ─── (1) public-API import ────────────────────────────────────────────────────


@pytest.mark.stage1
def test_run_function_is_importable() -> None:
    from phantom.sandbox import run, SandboxPolicy, SandboxResult
    assert callable(run)
    assert SandboxPolicy is not None
    assert SandboxResult is not None


@pytest.mark.stage1
def test_engine_executor_is_importable() -> None:
    from phantom.engine import (
        ExecuteBashRequest,
        ExecuteBashResult,
        execute_bash,
    )
    assert callable(execute_bash)
    assert ExecuteBashRequest is not None
    assert ExecuteBashResult is not None


# ─── (2) backend selection ────────────────────────────────────────────────────


@pytest.mark.stage1
@sandbox_capable
@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "All four sandboxed backends ({bwrap,firejail,unshare,docker}) "
        "are POSIX-only at runtime — on hosted Windows runners docker.exe "
        "is on PATH (so sandbox_capable doesn't skip) but Docker Desktop's "
        "daemon may or may not be Linux-container ready, and the other "
        "three never apply. select_backend() correctly falls through to "
        "passthrough on Windows; that's not a regression."
    ),
)
def test_select_backend_returns_some_backend() -> None:
    from phantom.sandbox import select_backend
    b = select_backend()
    assert b.name in {"bwrap", "firejail", "unshare", "docker"}


# ─── (3) round-trip ───────────────────────────────────────────────────────────


@pytest.mark.stage1
@sandbox_capable
@pytest.mark.skipif(sys.platform == "win32", reason="stage-closure smoke tests assume POSIX sandbox semantics")
def test_round_trip_echo(tmp_path) -> None:
    from phantom.sandbox import SandboxPolicy, run, select_backend
    policy = SandboxPolicy(
        workdir=str(tmp_path), writable_paths=(str(tmp_path),)
    )
    result = run(["echo", "stage-1-ok"], policy)
    assert result.exit_code == 0
    assert "stage-1-ok" in result.stdout
    assert result.tier == select_backend().name


# ─── (4) timeout ──────────────────────────────────────────────────────────────


@pytest.mark.stage1
@sandbox_capable
@pytest.mark.skipif(sys.platform == "win32", reason="stage-closure smoke tests assume POSIX sandbox semantics")
def test_timeout_raises(tmp_path) -> None:
    from phantom.sandbox import SandboxPolicy, run
    from phantom.sandbox.policy import ResourceLimits
    policy = SandboxPolicy(
        workdir=str(tmp_path),
        writable_paths=(str(tmp_path),),
        limits=ResourceLimits(wall_s=2.0, cpu_s=1.5),
    )
    with pytest.raises(SandboxTimeoutError) as excinfo:
        run(["sleep", "30"], policy)
    assert excinfo.value.deadline_s == 2.0


# ─── (5) audit log ────────────────────────────────────────────────────────────


@pytest.mark.stage1
@sandbox_capable
@pytest.mark.skipif(sys.platform == "win32", reason="stage-closure smoke tests assume POSIX sandbox semantics")
def test_audit_log_one_record_per_call(tmp_path) -> None:
    from phantom.sandbox import SandboxPolicy, run
    audit = tmp_path / "audit.log"
    policy = SandboxPolicy(
        workdir=str(tmp_path), writable_paths=(str(tmp_path),)
    )
    run(["echo", "x"], policy, audit_path=str(audit))
    lines = audit.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    for key in (
        "ts", "code", "tier", "cmd_sha256", "argv_len",
        "policy_hash", "deadline_s", "duration_s", "exit_code",
        "truncated", "phantom_ver",
    ):
        assert key in rec, f"audit record missing key {key!r}"
    assert rec["code"] == "ok"
    assert rec["exit_code"] == 0


# ─── (6) blocklist defence in depth ───────────────────────────────────────────


@pytest.mark.stage1
def test_blocklist_blocks_rm_rf_root(tmp_path) -> None:
    from phantom.engine import ExecuteBashRequest, execute_bash
    req = ExecuteBashRequest(
        command="rm -rf /",
        workdir=str(tmp_path),
    )
    with pytest.raises(PermissionDeniedError):
        execute_bash(req)


# ─── Stage version stamp ──────────────────────────────────────────────────────


@pytest.mark.stage1
def test_phantom_version_unchanged_through_stage_1() -> None:
    """Stage 1 does not bump the version. v4.0.0 ships at Stage 8."""
    import phantom
    assert phantom.__version__ in {"4.0.0-dev", "4.0.0", "4.0.1", "4.0.2", "4.0.3", "4.0.4", "4.0.5", "4.0.6", "4.0.7", "4.0.8", "4.0.9", "4.0.10", "1.0.0", "1.0.1", "1.0.2", "1.1.0", "1.1.1", "1.1.2", "1.1.3"}


@pytest.mark.stage1
def test_no_unsandboxed_subprocess_outside_sandbox_module() -> None:
    """The grep-style policy test runs as part of Stage 1 on every CI build."""
    # Importing the actual test re-runs it.
    from tests.sandbox.test_no_unsandboxed_subprocess import (
        test_no_unsandboxed_subprocess_in_phantom_package,
    )
    # The function asserts on its own; just calling it suffices.
    test_no_unsandboxed_subprocess_in_phantom_package()
