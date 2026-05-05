"""Tests for the passthrough sandbox backend (Windows fallback)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from phantom.errors import SandboxLaunchError, SandboxTimeoutError
from phantom.sandbox.backends.passthrough import PassthroughBackend, _warn_once
from phantom.sandbox.policy import ResourceLimits, SandboxPolicy


# ─── identity / tier rank ───────────────────────────────────────────────────


def test_passthrough_name_and_rank():
    b = PassthroughBackend()
    assert b.name == "passthrough"
    assert b.tier_rank == 99  # last-resort


def test_passthrough_in_global_registry():
    """Selector must know about passthrough so it can pick it on Windows."""
    from phantom.sandbox.backends import all_backends
    names = {b.name for b in all_backends()}
    assert "passthrough" in names


# ─── probe gating ──────────────────────────────────────────────────────────


def test_probe_false_on_linux_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("PHANTOM_ALLOW_PASSTHROUGH", raising=False)
    assert PassthroughBackend().probe() is False


def test_probe_true_on_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("PHANTOM_ALLOW_PASSTHROUGH", raising=False)
    assert PassthroughBackend().probe() is True


def test_probe_true_on_linux_with_env_var(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PHANTOM_ALLOW_PASSTHROUGH", "1")
    assert PassthroughBackend().probe() is True


# ─── launch behaviour ──────────────────────────────────────────────────────


@pytest.fixture
def passthrough_policy(tmp_path: Path) -> SandboxPolicy:
    return SandboxPolicy(
        workdir=str(tmp_path),
        writable_paths=(str(tmp_path),),
        limits=ResourceLimits(wall_s=5.0, cpu_s=5.0, rss_mib=128),
    )


def test_launch_runs_command_and_captures_stdout(passthrough_policy):
    b = PassthroughBackend()
    result = b.launch(["echo", "passthrough-hello"], passthrough_policy)
    assert result.exit_code == 0
    assert "passthrough-hello" in result.stdout
    assert result.tier == "passthrough"
    assert result.wall_s >= 0


def test_launch_propagates_nonzero_exit(passthrough_policy):
    b = PassthroughBackend()
    if sys.platform == "win32":
        result = b.launch(["cmd", "/c", "exit", "7"], passthrough_policy)
    else:
        result = b.launch(["sh", "-c", "exit 7"], passthrough_policy)
    assert result.exit_code == 7


def test_launch_empty_argv_raises(passthrough_policy):
    b = PassthroughBackend()
    with pytest.raises(SandboxLaunchError):
        b.launch([], passthrough_policy)


def test_launch_unknown_binary_raises_launch_error(passthrough_policy):
    b = PassthroughBackend()
    with pytest.raises(SandboxLaunchError):
        b.launch(["this-binary-does-not-exist-xyz"], passthrough_policy)


@pytest.mark.skipif(sys.platform == "win32", reason="subprocess timeout signalling on Windows differs; tracked as TODO for v1.2")
def test_launch_timeout(tmp_path: Path):
    policy = SandboxPolicy(
        workdir=str(tmp_path),
        writable_paths=(str(tmp_path),),
        limits=ResourceLimits(wall_s=0.1, cpu_s=0.1, rss_mib=64),
    )
    b = PassthroughBackend()
    cmd = ["sh", "-c", "sleep 5"] if sys.platform != "win32" else ["timeout", "/t", "5"]
    with pytest.raises(SandboxTimeoutError):
        b.launch(cmd, policy)


# ─── env handling ──────────────────────────────────────────────────────────


def test_env_inherited_when_value_is_none(passthrough_policy, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHANTOM_TEST_INHERIT", "yes")
    policy = SandboxPolicy(
        workdir=passthrough_policy.workdir,
        writable_paths=passthrough_policy.writable_paths,
        env={"PHANTOM_TEST_INHERIT": None},
        limits=passthrough_policy.limits,
    )
    b = PassthroughBackend()
    cmd = (["sh", "-c", "echo $PHANTOM_TEST_INHERIT"]
           if sys.platform != "win32"
           else ["cmd", "/c", "echo %PHANTOM_TEST_INHERIT%"])
    result = b.launch(cmd, policy)
    assert "yes" in result.stdout


def test_env_explicit_value_overrides(passthrough_policy):
    policy = SandboxPolicy(
        workdir=passthrough_policy.workdir,
        writable_paths=passthrough_policy.writable_paths,
        env={"PHANTOM_TEST_VAL": "explicit"},
        limits=passthrough_policy.limits,
    )
    b = PassthroughBackend()
    cmd = (["sh", "-c", "echo $PHANTOM_TEST_VAL"]
           if sys.platform != "win32"
           else ["cmd", "/c", "echo %PHANTOM_TEST_VAL%"])
    result = b.launch(cmd, policy)
    assert "explicit" in result.stdout


def test_path_inherited_implicitly(passthrough_policy):
    """Passthrough must include PATH even if the policy didn't list it,
    or commands won't be findable on Windows."""
    b = PassthroughBackend()
    # echo (POSIX) / echo (Windows cmd builtin) — both need PATH if we
    # don't shell out via /bin/sh. We use shell=False, so the OS PATH
    # is what locates the binary.
    result = b.launch(["echo", "ok"], passthrough_policy)
    assert result.exit_code == 0


# ─── warning emission ──────────────────────────────────────────────────────


def test_warning_emitted_only_once(monkeypatch: pytest.MonkeyPatch, caplog):
    """Repeated launches must not spam the log."""
    import phantom.sandbox.backends.passthrough as p
    monkeypatch.setattr(p, "_WARNED_ONCE", False)
    p._warn_once()
    p._warn_once()
    p._warn_once()
    assert sum(1 for r in caplog.records if "PASSTHROUGH mode" in r.getMessage()) <= 1


# ─── result truncation ─────────────────────────────────────────────────────


def test_truncate_short_text():
    b = PassthroughBackend()
    text, truncated = b._truncate("hi")
    assert text == "hi"
    assert truncated is False


def test_truncate_caps_at_limit():
    b = PassthroughBackend()
    big = "x" * 2_000_000
    text, truncated = b._truncate(big)
    assert truncated is True
    assert len(text) == 1_048_576
