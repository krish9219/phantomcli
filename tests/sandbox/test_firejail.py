"""Tests for the ``firejail`` backend.

Firejail is not always available; these tests are skipped when it is
not. The contract tests in ``test_run_contract.py`` exercise behavioural
parity when firejail is present.
"""

from __future__ import annotations

import shutil

import pytest

from phantom.sandbox.backends.firejail import FirejailBackend


firejail_available = pytest.mark.skipif(
    shutil.which("firejail") is None, reason="firejail not installed"
)


class TestFirejailMetadata:
    def test_name_and_rank(self):
        b = FirejailBackend()
        assert b.name == "firejail"
        assert b.tier_rank == 2

    @firejail_available
    def test_probe_true(self):
        assert FirejailBackend().probe() is True

    def test_probe_false_when_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert FirejailBackend().probe() is False
