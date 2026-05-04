"""Tests for :mod:`phantom.sandbox.limits`.

Coverage target: 100% line, 100% branch.
"""

from __future__ import annotations

import pytest

from phantom.sandbox.limits import (
    docker_flags,
    prlimit_args,
    ulimit_shell_prefix,
)
from phantom.sandbox.policy import ResourceLimits


# ─── prlimit_args ─────────────────────────────────────────────────────────────


class TestPrlimitArgs:
    def test_full_limits(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=20.0, rss_mib=512, fds=256)
        args = prlimit_args(rl)
        assert args == ["prlimit", "--cpu=20", "--as=536870912", "--nofile=256"]

    def test_no_cpu(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=None, rss_mib=512, fds=256)
        args = prlimit_args(rl)
        assert "--cpu=20" not in " ".join(args)
        assert "--as=536870912" in args
        assert "--nofile=256" in args

    def test_no_rss(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=10.0, rss_mib=None, fds=256)
        args = prlimit_args(rl)
        assert "--cpu=10" in args
        assert all(not a.startswith("--as=") for a in args)
        assert "--nofile=256" in args

    def test_no_fds(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=10.0, rss_mib=128, fds=None)
        args = prlimit_args(rl)
        assert all(not a.startswith("--nofile=") for a in args)

    def test_minimum_args_when_all_optional_none(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=None, rss_mib=None, fds=None)
        args = prlimit_args(rl)
        assert args == ["prlimit"]

    def test_cpu_truncated_to_int(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=15.7)
        args = prlimit_args(rl)
        assert "--cpu=15" in args


# ─── ulimit_shell_prefix ──────────────────────────────────────────────────────


class TestUlimitShellPrefix:
    def test_full_prefix(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=20.0, rss_mib=512, fds=256)
        prefix = ulimit_shell_prefix(rl)
        # Joined with '; ' and ends with '; '
        assert "ulimit -t 20" in prefix
        assert "ulimit -v 524288" in prefix  # 512 MiB in KiB
        assert "ulimit -n 256" in prefix
        assert prefix.endswith("; ")

    def test_empty_when_no_limits(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=None, rss_mib=None, fds=None)
        assert ulimit_shell_prefix(rl) == ""

    def test_partial_only_cpu(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=10.0, rss_mib=None, fds=None)
        prefix = ulimit_shell_prefix(rl)
        assert prefix == "ulimit -t 10; "


# ─── docker_flags ─────────────────────────────────────────────────────────────


class TestDockerFlags:
    def test_full_limits(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=20.0, rss_mib=512, fds=256)
        flags = docker_flags(rl)
        assert flags == [
            "--ulimit", "cpu=20:20",
            "--memory", "512m",
            "--ulimit", "nofile=256:256",
        ]

    def test_no_cpu(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=None, rss_mib=512, fds=256)
        flags = docker_flags(rl)
        assert "--memory" in flags
        # No cpu ulimit pair
        assert "cpu=" + "0:0" not in " ".join(flags)

    def test_empty_when_all_none(self):
        rl = ResourceLimits(wall_s=30.0, cpu_s=None, rss_mib=None, fds=None)
        assert docker_flags(rl) == []

    def test_invalid_limits_blocked_at_construction(self):
        with pytest.raises(Exception):
            ResourceLimits(wall_s=0)
