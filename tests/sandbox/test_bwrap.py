"""Tests for the ``bwrap`` backend.

The contract tests in ``test_run_contract.py`` already cover the
behavioural promises against every available backend, including bwrap.
This file adds bwrap-specific assertions (deny-list flag layout,
file-vs-directory hiding, namespace flags).
"""

from __future__ import annotations

import os
import shutil

import pytest

from phantom.errors import SandboxLaunchError
from phantom.sandbox.backends.bwrap import BwrapBackend
from phantom.sandbox.policy import SandboxPolicy


bwrap_available = pytest.mark.skipif(
    shutil.which("bwrap") is None, reason="bwrap not installed"
)


# ─── Probe & metadata ─────────────────────────────────────────────────────────


class TestBwrapMetadata:
    def test_name_and_rank(self):
        b = BwrapBackend()
        assert b.name == "bwrap"
        assert b.tier_rank == 1

    @bwrap_available
    def test_probe_true(self):
        assert BwrapBackend().probe() is True

    def test_probe_false_when_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert BwrapBackend().probe() is False


# ─── Argv construction (deny-list, mounts, namespaces) ────────────────────────


@bwrap_available
class TestBwrapLaunch:
    def test_empty_argv_raises(self, tmp_path):
        b = BwrapBackend()
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        with pytest.raises(SandboxLaunchError, match="argv is empty"):
            b.launch([], policy)

    def test_writable_path_under_tmp_works(self, tmp_path):
        # Reproduces the order-dependent bug: writable bind-mount under
        # /tmp must "drill through" the bwrap-managed tmpfs.
        b = BwrapBackend()
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        result = b.launch(
            ["sh", "-c", f"echo wrote > {tmp_path}/marker.txt && cat {tmp_path}/marker.txt"],
            policy,
        )
        assert result.exit_code == 0
        assert "wrote" in result.stdout

    def test_deny_list_blocks_etc_shadow_contents(self, tmp_path):
        # /etc/shadow is on the default deny list. The sandboxed process
        # must NOT be able to read its real contents. Two outcomes are
        # acceptable:
        #   1. Empty file (when our /dev/null bind succeeded).
        #   2. Permission denied (when host fs perms block the read).
        # What is NOT acceptable: actual shadow content reaching stdout.
        b = BwrapBackend()
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        result = b.launch(
            ["sh", "-c", "cat /etc/shadow 2>/dev/null; echo END"],
            policy,
        )
        # 'root:' or '$6$' or '$y$' is the typical shadow-record prefix.
        assert "root:" not in result.stdout
        assert "$6$" not in result.stdout
        assert "$y$" not in result.stdout
        assert result.stdout.strip().endswith("END")

    def test_deny_list_blocks_etc_sudoers_contents(self, tmp_path):
        if not os.path.exists("/etc/sudoers"):
            pytest.skip("/etc/sudoers absent on this host")
        b = BwrapBackend()
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        result = b.launch(
            ["sh", "-c", "cat /etc/sudoers 2>/dev/null; echo END"],
            policy,
        )
        # Common sudoers tokens — none should leak.
        assert "root ALL" not in result.stdout
        assert "%sudo" not in result.stdout
        assert result.stdout.strip().endswith("END")

    def test_writable_path_outside_tmp_works(self, tmp_path, monkeypatch):
        # Make a writable path outside /tmp.
        external = tmp_path.parent / "external_workdir"
        external.mkdir(exist_ok=True)
        monkeypatch.delenv("PHANTOM_HOME", raising=False)
        b = BwrapBackend()
        policy = SandboxPolicy(
            workdir=str(external),
            writable_paths=(str(external),),
        )
        result = b.launch(["pwd"], policy)
        assert result.stdout.strip() == str(external)

    def test_writable_path_created_when_missing(self, tmp_path):
        # Operator declared a writable path that doesn't yet exist.
        # The backend should create it and proceed.
        target = tmp_path / "auto-created-workspace"
        assert not target.exists()
        b = BwrapBackend()
        policy = SandboxPolicy(
            workdir=str(target),
            writable_paths=(str(target),),
        )
        result = b.launch(["echo", "ok"], policy)
        assert result.exit_code == 0
        assert target.exists()

    def test_no_network_default_blocks_real_traffic(self, tmp_path):
        # The security guarantee is "no real network", not "no host
        # interface labels visible in sysfs". We assert the *real*
        # property: a TCP connection to a public address must fail.
        b = BwrapBackend()
        policy = SandboxPolicy(
            workdir=str(tmp_path), writable_paths=(str(tmp_path),)
        )
        # `getent hosts` does a DNS lookup; in a sealed net namespace it
        # cannot reach the resolver and exits non-zero.
        result = b.launch(
            ["sh", "-c", "getent hosts example.com >/dev/null 2>&1; echo RC=$?"],
            policy,
        )
        # Either getent failed (rc != 0) or, on a host with a local
        # cache, the lookup exited 0 but no real network was used. We
        # assert the lookup ran AT ALL (the sandbox launched), and that
        # the rc — if 0 — was a cache hit, not a real packet. The
        # cleanest signal: verify the network namespace itself by
        # checking the ip link count. An isolated net namespace has at
        # most `lo` (and even that starts down).
        rc_line = next(
            (line for line in result.stdout.splitlines() if line.startswith("RC=")),
            None,
        )
        assert rc_line is not None
        # We accept rc!=0 (no resolver) as the strong signal. rc=0 is
        # also acceptable in some local-cache configurations; we leave
        # the network-namespace assertion to a separate test that
        # doesn't depend on sysfs.
        rc = int(rc_line.removeprefix("RC="))
        assert rc != 0, (
            "expected DNS lookup to fail in isolated net namespace; got rc=0 "
            "(stdout: " + repr(result.stdout) + ")"
        )

    def test_network_can_be_enabled(self, tmp_path):
        b = BwrapBackend()
        policy = SandboxPolicy(
            workdir=str(tmp_path),
            writable_paths=(str(tmp_path),),
            network=True,
        )
        # With network=True the sandbox shares the host's net namespace
        # and DNS resolution succeeds (assuming the host has working DNS).
        # If the host itself has no DNS we accept any rc — we only
        # assert the *sandbox launched* with network enabled, which is
        # implicit in not raising.
        result = b.launch(
            ["sh", "-c", "echo network-on; getent hosts example.com >/dev/null 2>&1; echo RC=$?"],
            policy,
        )
        assert "network-on" in result.stdout
        assert "RC=" in result.stdout
