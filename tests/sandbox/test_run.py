"""Tests for :func:`phantom.sandbox.run` — the public entry point.

Covers the audit-log integration, exception propagation, and backend
override paths. Exercises the live unshare backend where possible.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from phantom.errors import (
    SandboxLaunchError,
    SandboxTimeoutError,
    SandboxUnavailableError,
)
from phantom.sandbox import (
    SandboxPolicy,
    SandboxResult,
    clear_cache_for_tests,
    run,
)
from phantom.sandbox._backend import SandboxBackend
from phantom.sandbox.backends.unshare import UnshareBackend
from phantom.sandbox.policy import ResourceLimits


unshare_available = pytest.mark.skipif(
    shutil.which("unshare") is None or shutil.which("prlimit") is None,
    reason="unshare/prlimit not available",
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Each test gets a fresh PHANTOM_HOME and a clean selection cache."""
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    monkeypatch.delenv("PHANTOM_SANDBOX_TIER", raising=False)
    clear_cache_for_tests()
    yield
    clear_cache_for_tests()


# ─── Public-API smoke tests ───────────────────────────────────────────────────


@unshare_available
class TestRunHappyPath:
    def test_echo(self, tmp_path):
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        result = run(["echo", "hi"], policy)
        assert isinstance(result, SandboxResult)
        assert result.exit_code == 0
        assert result.stdout.strip() == "hi"
        assert result.tier in {"unshare", "bwrap", "firejail", "docker"}

    def test_explicit_backend(self, tmp_path):
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        result = run(["echo", "hi"], policy, backend=UnshareBackend())
        assert result.tier == "unshare"

    def test_empty_argv_raises(self, tmp_path):
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        with pytest.raises(SandboxLaunchError, match="argv is empty"):
            run([], policy)


# ─── Audit-log integration ────────────────────────────────────────────────────


@unshare_available
class TestRunAuditIntegration:
    def test_one_record_per_call(self, tmp_path):
        audit = tmp_path / "audit.log"
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        run(["echo", "x"], policy, audit_path=str(audit))
        run(["echo", "y"], policy, audit_path=str(audit))
        lines = audit.read_text().splitlines()
        assert len(lines) == 2

    def test_audit_record_has_expected_fields(self, tmp_path):
        audit = tmp_path / "audit.log"
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        run(["echo", "ok"], policy, audit_path=str(audit))
        rec = json.loads(audit.read_text().splitlines()[0])
        for key in (
            "ts",
            "code",
            "tier",
            "cmd_sha256",
            "argv_len",
            "policy_hash",
            "deadline_s",
            "duration_s",
            "exit_code",
            "truncated",
            "phantom_ver",
        ):
            assert key in rec, f"audit record missing key {key!r}"
        assert rec["code"] == "ok"
        assert rec["exit_code"] == 0

    def test_audit_records_nonzero_exit_with_special_code(self, tmp_path):
        audit = tmp_path / "audit.log"
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        run(["sh", "-c", "exit 3"], policy, audit_path=str(audit))
        rec = json.loads(audit.read_text().splitlines()[0])
        assert rec["code"] == "phantom.sandbox.nonzero_exit"
        assert rec["exit_code"] == 3

    def test_audit_records_timeout(self, tmp_path):
        audit = tmp_path / "audit.log"
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            limits=ResourceLimits(wall_s=2.0, cpu_s=1.5),
        )
        with pytest.raises(SandboxTimeoutError):
            run(["sleep", "30"], policy, audit_path=str(audit))
        rec = json.loads(audit.read_text().splitlines()[0])
        assert rec["code"] == "phantom.sandbox.timeout"
        assert rec["exit_code"] is None

    def test_audit_disabled_when_capture_audit_false(self, tmp_path):
        # audit_path=None and capture_audit=False → no log written.
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            capture_audit=False,
        )
        run(["echo", "ok"], policy)
        # Default audit path under PHANTOM_HOME should not have been created.
        default = Path(tmp_path) / ".phantom" / "sandbox-audit.log"
        assert not default.exists()


# ─── Backend selection failure path ───────────────────────────────────────────


class TestRunSelectionFailure:
    def test_raises_when_no_backend_available(self, tmp_path, monkeypatch):
        # Simulate "no backends available" by patching the registry.
        from phantom.sandbox import select as select_mod

        class _NoneAvailable(SandboxBackend):
            name = "test-na"  # type: ignore[misc]
            tier_rank = 99  # type: ignore[misc]

            def probe(self) -> bool:
                return False

            def launch(self, argv, policy):  # noqa: ARG002
                raise NotImplementedError

        monkeypatch.setattr(
            select_mod,
            "all_backends",
            lambda: [_NoneAvailable()],
        )
        clear_cache_for_tests()

        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        with pytest.raises(SandboxUnavailableError):
            run(["echo", "x"], policy)
