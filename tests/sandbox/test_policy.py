"""Tests for :mod:`phantom.sandbox.policy`.

Coverage target: 100% line, 100% branch on `phantom.sandbox.policy`.
"""

from __future__ import annotations

import os
import sys

import pytest

from phantom.errors import ConfigError, PhantomError
from phantom.sandbox.policy import (
    DEFAULT_DENY_PATHS,
    ResourceLimits,
    SandboxPolicy,
)


# Platform-portable absolute paths. ``/tmp/job`` etc. are absolute on
# POSIX but not on Windows (no drive letter). ``os.path.abspath``
# anchors them to the current drive on Windows, and is a no-op on POSIX.
_JOB = os.path.abspath("/tmp/job")
_JOB_SUB = os.path.abspath("/tmp/job/sub")
_JOB_TRAILING = _JOB + os.sep  # what we *pass in* to test trailing-slash normalisation
_VAR_TMP = os.path.abspath("/var/tmp")
_DATA = os.path.abspath("/data")
_DATA_TRAILING = _DATA + os.sep
_ROOT = os.path.abspath(os.sep)


# ─── ResourceLimits ───────────────────────────────────────────────────────────


class TestResourceLimits:
    def test_defaults_are_sensible(self):
        rl = ResourceLimits()
        assert rl.wall_s == 300.0
        assert rl.cpu_s == 60.0
        assert rl.rss_mib == 512
        assert rl.fds == 256
        assert rl.stdout_bytes == 1024 * 1024
        assert rl.stderr_bytes == 1024 * 1024
        assert rl.nofork is False

    def test_frozen(self):
        rl = ResourceLimits()
        with pytest.raises(Exception):
            rl.wall_s = 1.0  # type: ignore[misc]

    def test_wall_s_must_be_positive(self):
        with pytest.raises(ConfigError, match="wall_s must be > 0"):
            ResourceLimits(wall_s=0)
        with pytest.raises(ConfigError, match="wall_s must be > 0"):
            ResourceLimits(wall_s=-1)

    def test_cpu_s_must_be_positive(self):
        with pytest.raises(ConfigError, match="cpu_s must be > 0"):
            ResourceLimits(cpu_s=0)
        with pytest.raises(ConfigError, match="cpu_s must be > 0"):
            ResourceLimits(cpu_s=-5)

    def test_cpu_s_can_be_none(self):
        rl = ResourceLimits(cpu_s=None)
        assert rl.cpu_s is None

    def test_cpu_s_must_not_exceed_wall_s(self):
        with pytest.raises(ConfigError, match="cpu_s .* must be ≤ wall_s"):
            ResourceLimits(wall_s=10.0, cpu_s=20.0)

    def test_cpu_s_equal_to_wall_s_is_ok(self):
        rl = ResourceLimits(wall_s=10.0, cpu_s=10.0)
        assert rl.cpu_s == rl.wall_s

    def test_rss_mib_validation(self):
        with pytest.raises(ConfigError, match="rss_mib must be > 0"):
            ResourceLimits(rss_mib=0)
        with pytest.raises(ConfigError, match="rss_mib must be ≤ 16384"):
            ResourceLimits(rss_mib=17000)

    def test_rss_mib_can_be_none(self):
        rl = ResourceLimits(rss_mib=None)
        assert rl.rss_mib is None

    def test_fds_validation(self):
        with pytest.raises(ConfigError, match="fds must be > 0"):
            ResourceLimits(fds=0)
        with pytest.raises(ConfigError, match="fds must be ≤ 65535"):
            ResourceLimits(fds=70000)

    def test_fds_can_be_none(self):
        rl = ResourceLimits(fds=None)
        assert rl.fds is None

    def test_stdout_bytes_minimum(self):
        with pytest.raises(ConfigError, match="stdout_bytes must be ≥ 4096"):
            ResourceLimits(stdout_bytes=100)

    def test_stderr_bytes_minimum(self):
        with pytest.raises(ConfigError, match="stderr_bytes must be ≥ 4096"):
            ResourceLimits(stderr_bytes=100)

    def test_inherits_from_phantom_error(self):
        # The exceptions must be PhantomError so callers can catch the base.
        try:
            ResourceLimits(wall_s=0)
        except PhantomError:
            pass
        else:
            pytest.fail("ResourceLimits should raise a PhantomError subclass")


# ─── SandboxPolicy ────────────────────────────────────────────────────────────


class TestSandboxPolicyConstruction:
    def test_minimal_valid_policy(self):
        p = SandboxPolicy(workdir=_JOB, writable_paths=(_JOB,))
        assert p.workdir == _JOB
        assert p.network is False
        assert p.capture_audit is True
        assert p.raise_on_truncation is False

    def test_workdir_required(self):
        with pytest.raises(ConfigError, match="workdir is required"):
            SandboxPolicy(workdir="")

    def test_workdir_must_be_absolute(self):
        with pytest.raises(ConfigError, match="must be absolute"):
            SandboxPolicy(workdir="relative/path", writable_paths=("relative/path",))

    def test_workdir_must_be_in_writable_paths(self):
        with pytest.raises(ConfigError, match="must be inside one of writable_paths"):
            SandboxPolicy(workdir=_JOB, writable_paths=(_VAR_TMP,))

    def test_workdir_at_writable_path_root_allowed(self):
        p = SandboxPolicy(workdir=_JOB, writable_paths=(_JOB,))
        assert p.workdir == _JOB

    def test_workdir_in_subdirectory_of_writable_allowed(self):
        p = SandboxPolicy(workdir=_JOB_SUB, writable_paths=(_JOB,))
        assert p.workdir == _JOB_SUB

    def test_workdir_with_trailing_slash_in_writable_normalised(self):
        p = SandboxPolicy(workdir=_JOB, writable_paths=(_JOB_TRAILING,))
        assert p.workdir == _JOB

    def test_workdir_root_writable_allowed(self):
        p = SandboxPolicy(workdir=_JOB, writable_paths=(_ROOT,))
        assert p.workdir == _JOB

    def test_overlapping_paths_rejected(self):
        with pytest.raises(ConfigError, match="in both writable_paths and read_only_paths"):
            SandboxPolicy(
                workdir=_DATA,
                writable_paths=(_DATA,),
                read_only_paths=(_DATA,),
            )

    def test_overlapping_with_trailing_slashes(self):
        with pytest.raises(ConfigError, match="in both writable_paths and read_only_paths"):
            SandboxPolicy(
                workdir=_DATA,
                writable_paths=(_DATA_TRAILING,),
                read_only_paths=(_DATA,),
            )


class TestSandboxPolicyImmutability:
    def test_policy_is_frozen(self):
        p = SandboxPolicy(workdir=_JOB, writable_paths=(_JOB,))
        with pytest.raises(Exception):
            p.network = True  # type: ignore[misc]


# DEFAULT_DENY_PATHS expansion is hard-coded for POSIX home layouts —
# the implementation does ``home + "/.ssh"`` style concatenation and the
# defaults reference ``/etc/shadow`` etc. On Windows, callers would
# typically use Windows-native deny lists; that surface area is out of
# scope for these unit tests.
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="DEFAULT_DENY_PATHS expansion is POSIX-style.",
)
class TestExpandedDenyPaths:
    def test_default_deny_list_is_applied(self):
        p = SandboxPolicy(workdir="/tmp/job", writable_paths=("/tmp/job",))
        out = p.expanded_deny_paths(home="/home/alice")
        # Every default tilde path should be expanded.
        assert "/home/alice/.ssh" in out
        assert "/home/alice/.aws" in out
        assert "/etc/shadow" in out

    def test_user_deny_list_extends_defaults(self):
        p = SandboxPolicy(
            workdir="/tmp/job",
            writable_paths=("/tmp/job",),
            deny_paths=("~/secrets", "/var/secrets"),
        )
        out = p.expanded_deny_paths(home="/home/alice")
        assert "/home/alice/secrets" in out
        assert "/var/secrets" in out

    def test_dedupes_overlap_between_defaults_and_user(self):
        # ~/.ssh is in DEFAULT_DENY_PATHS; declaring it again should not duplicate.
        p = SandboxPolicy(
            workdir="/tmp/job",
            writable_paths=("/tmp/job",),
            deny_paths=("~/.ssh",),
        )
        out = p.expanded_deny_paths(home="/home/alice")
        assert out.count("/home/alice/.ssh") == 1

    def test_home_with_trailing_slash_normalised(self):
        p = SandboxPolicy(workdir="/tmp/job", writable_paths=("/tmp/job",))
        out = p.expanded_deny_paths(home="/home/alice/")
        assert "/home/alice/.ssh" in out

    def test_home_root_edge_case(self):
        # A user with home '/' (unusual, but possible for root in some envs)
        # — should not produce paths like '//.ssh'.
        p = SandboxPolicy(workdir="/tmp/job", writable_paths=("/tmp/job",))
        out = p.expanded_deny_paths(home="/")
        assert "//.ssh" not in out


class TestDefaultDenyPaths:
    def test_default_includes_phantom_home(self):
        # The agent should never be able to rewrite its own license/audit
        # log/memory DB from inside a sandboxed tool.
        assert "~/.phantom" in DEFAULT_DENY_PATHS
        assert "~/.omnicli" in DEFAULT_DENY_PATHS

    def test_default_includes_ssh_keys(self):
        assert "~/.ssh" in DEFAULT_DENY_PATHS

    def test_default_includes_cloud_creds(self):
        assert "~/.aws" in DEFAULT_DENY_PATHS
        assert "~/.azure" in DEFAULT_DENY_PATHS
        assert "~/.config/gcloud" in DEFAULT_DENY_PATHS
        assert "~/.kube" in DEFAULT_DENY_PATHS

    def test_default_includes_shadow_and_sudoers(self):
        assert "/etc/shadow" in DEFAULT_DENY_PATHS
        assert "/etc/sudoers" in DEFAULT_DENY_PATHS
