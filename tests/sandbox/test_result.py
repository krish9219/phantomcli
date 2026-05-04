"""Tests for :mod:`phantom.sandbox.result`.

Coverage target: 100% line, 100% branch.
"""

from __future__ import annotations

import pytest

from phantom.sandbox.result import SandboxResult


class TestSandboxResult:
    def test_construction(self):
        r = SandboxResult(
            stdout="ok\n",
            stderr="",
            exit_code=0,
            wall_s=0.01,
            tier="unshare",
            truncated=False,
        )
        assert r.stdout == "ok\n"
        assert r.exit_code == 0
        assert r.wall_s == 0.01
        assert r.tier == "unshare"
        assert r.truncated is False

    def test_ok_property_true_for_zero_exit(self):
        r = SandboxResult(
            stdout="", stderr="", exit_code=0, wall_s=0.0, tier="t", truncated=False
        )
        assert r.ok is True

    def test_ok_property_false_for_nonzero_exit(self):
        r = SandboxResult(
            stdout="", stderr="", exit_code=1, wall_s=0.0, tier="t", truncated=False
        )
        assert r.ok is False

    def test_ok_property_false_for_signal_exit(self):
        # 128 + 9 = SIGKILL
        r = SandboxResult(
            stdout="", stderr="", exit_code=137, wall_s=0.0, tier="t", truncated=False
        )
        assert r.ok is False

    def test_frozen(self):
        r = SandboxResult(
            stdout="", stderr="", exit_code=0, wall_s=0.0, tier="t", truncated=False
        )
        with pytest.raises(Exception):
            r.exit_code = 1  # type: ignore[misc]
