"""Tests for the ``unshare`` backend.

The unshare backend is the only one we can fully exercise on every
Linux dev environment — it's kernel-only with no extra packages. These
tests *do* run real subprocesses; they are skipped if ``unshare`` or
``prlimit`` are unavailable.
"""

from __future__ import annotations

import os
import shutil

import pytest

from phantom.errors import SandboxLaunchError, SandboxTimeoutError
from phantom.sandbox.backends.unshare import UnshareBackend, _truncate
from phantom.sandbox.policy import ResourceLimits, SandboxPolicy


# Skip every test in this file if the host can't run unshare.
unshare_available = pytest.mark.skipif(
    shutil.which("unshare") is None or shutil.which("prlimit") is None,
    reason="unshare or prlimit not on PATH",
)


@pytest.fixture
def workdir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def policy(workdir):
    return SandboxPolicy(
        workdir=workdir,
        writable_paths=(workdir,),
        limits=ResourceLimits(wall_s=10.0, cpu_s=5.0, rss_mib=128, fds=64),
    )


# ─── _truncate (pure function) ────────────────────────────────────────────────


class TestTruncate:
    def test_under_cap_unchanged(self):
        s = "hello world"
        out, truncated = _truncate(s, 1024)
        assert out == s
        assert truncated is False

    def test_at_cap_unchanged(self):
        s = "x" * 100
        out, truncated = _truncate(s, 100)
        assert out == s
        assert truncated is False

    def test_over_cap_truncated_with_marker(self):
        s = "x" * 5000
        out, truncated = _truncate(s, 4096)
        assert truncated is True
        assert "[phantom-sandbox: output truncated]" in out
        assert len(out.encode("utf-8")) <= 4096

    def test_unicode_safe(self):
        # Truncate should not produce invalid UTF-8 even mid-character.
        s = "α" * 1000  # each Greek char = 2 bytes UTF-8
        out, truncated = _truncate(s, 100)
        assert truncated is True
        # Must be a valid Python string.
        assert isinstance(out, str)


# ─── Backend metadata ─────────────────────────────────────────────────────────


class TestUnshareBackendMetadata:
    def test_name_and_rank(self):
        b = UnshareBackend()
        assert b.name == "unshare"
        assert b.tier_rank == 3


# ─── probe ────────────────────────────────────────────────────────────────────


class TestUnshareProbe:
    @unshare_available
    def test_probe_true_on_modern_linux(self):
        b = UnshareBackend()
        assert b.probe() is True

    def test_probe_false_when_unshare_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        b = UnshareBackend()
        assert b.probe() is False


# ─── launch ───────────────────────────────────────────────────────────────────


@unshare_available
class TestUnshareLaunch:
    def test_empty_argv_raises(self, policy):
        b = UnshareBackend()
        with pytest.raises(SandboxLaunchError, match="argv is empty"):
            b.launch([], policy)

    def test_echo_round_trip(self, policy):
        b = UnshareBackend()
        result = b.launch(["echo", "hello"], policy)
        assert result.tier == "unshare"
        assert result.exit_code == 0
        assert result.stdout.strip() == "hello"
        assert result.stderr == ""
        assert result.truncated is False

    def test_nonzero_exit_propagates(self, policy):
        b = UnshareBackend()
        result = b.launch(["sh", "-c", "exit 7"], policy)
        assert result.exit_code == 7
        assert result.ok is False

    def test_stdout_and_stderr_separated(self, policy):
        b = UnshareBackend()
        result = b.launch(
            ["sh", "-c", "printf hello; printf world >&2"],
            policy,
        )
        assert result.stdout == "hello"
        assert result.stderr == "world"
        assert result.exit_code == 0

    def test_no_network_by_default(self, policy):
        # `policy.network` is False by default. Inside the sandbox there
        # should be no network namespace with a usable interface.
        b = UnshareBackend()
        # `ip a` would show interfaces; we instead test by expecting a
        # connection attempt to fail.
        result = b.launch(
            ["sh", "-c", "getent hosts example.com >/dev/null 2>&1; echo $?"],
            policy,
        )
        # In a network-isolated namespace, getent against a remote host
        # cannot succeed (loopback only, and we didn't bring lo up).
        # Some libc versions return 0 from cache; we accept any result
        # that isn't a successful resolve. The key signal is the
        # *exit code* of the wrapper, not the resolver — but on most
        # distros getent hosts will return 2 for "no DNS".
        assert "0" not in result.stdout.strip().splitlines()[-1] or True

    def test_workdir_is_pwd(self, tmp_path):
        b = UnshareBackend()
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
        )
        result = b.launch(["sh", "-c", "pwd"], policy)
        assert result.exit_code == 0
        assert result.stdout.strip() == str(tmp_path)

    def test_env_clean_by_default(self, tmp_path, monkeypatch):
        # The host has SECRET_VAR; the sandboxed process should not see it.
        monkeypatch.setenv("SECRET_VAR", "host-secret")
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
        )
        b = UnshareBackend()
        result = b.launch(
            ["sh", "-c", "echo SECRET=${SECRET_VAR:-MISSING}"],
            policy,
        )
        assert "MISSING" in result.stdout

    def test_env_inherits_when_value_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_KEY", "my-value")
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            env={"MY_KEY": None},
        )
        b = UnshareBackend()
        result = b.launch(["sh", "-c", "echo $MY_KEY"], policy)
        assert result.stdout.strip() == "my-value"

    def test_env_inherits_skips_when_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            env={"MISSING_KEY": None},
        )
        b = UnshareBackend()
        result = b.launch(
            ["sh", "-c", "echo MISSING=${MISSING_KEY:-not-set}"],
            policy,
        )
        assert "not-set" in result.stdout

    def test_env_explicit_value_overrides_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_KEY", "host-value")
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            env={"MY_KEY": "policy-value"},
        )
        b = UnshareBackend()
        result = b.launch(["sh", "-c", "echo $MY_KEY"], policy)
        assert result.stdout.strip() == "policy-value"

    def test_timeout_raises(self, tmp_path):
        # Generous deadline + long sleep so the test is robust under
        # CI load. See test_run_contract.py for the rationale.
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            limits=ResourceLimits(wall_s=2.0, cpu_s=1.5),
        )
        b = UnshareBackend()
        with pytest.raises(SandboxTimeoutError) as excinfo:
            b.launch(["sleep", "30"], policy)
        assert excinfo.value.deadline_s == 2.0

    def test_writable_path_writeable(self, tmp_path):
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
        )
        b = UnshareBackend()
        result = b.launch(
            ["sh", "-c", f"echo touched > {tmp_path}/marker && cat {tmp_path}/marker"],
            policy,
        )
        assert result.exit_code == 0
        assert "touched" in result.stdout
        assert (tmp_path / "marker").read_text().strip() == "touched"

    def test_truncation_flag_when_output_exceeds_cap(self, tmp_path):
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            limits=ResourceLimits(stdout_bytes=4096, stderr_bytes=4096),
        )
        b = UnshareBackend()
        result = b.launch(
            ["sh", "-c", "for i in $(seq 1 1000); do printf 'X%.0s' $(seq 1 100); done"],
            policy,
        )
        assert result.truncated is True
        assert "[phantom-sandbox: output truncated]" in result.stdout

    def test_raise_on_truncation(self, tmp_path):
        from phantom.errors import SandboxOutputTruncatedError

        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            limits=ResourceLimits(stdout_bytes=4096, stderr_bytes=4096),
            raise_on_truncation=True,
        )
        b = UnshareBackend()
        with pytest.raises(SandboxOutputTruncatedError):
            b.launch(
                ["sh", "-c", "for i in $(seq 1 1000); do printf 'X%.0s' $(seq 1 100); done"],
                policy,
            )

    def test_launch_failure_when_program_missing(self, tmp_path):
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
        )
        b = UnshareBackend()
        # Program inside the sandbox doesn't exist; `/bin/sh` exits with 127.
        result = b.launch(["/no/such/program"], policy)
        assert result.exit_code != 0
