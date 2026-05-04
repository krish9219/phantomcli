"""Tests for the ``docker`` backend.

Docker is not always available; these tests probe and skip otherwise.
The behavioural contract is enforced by ``test_run_contract.py`` when
docker is present.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from phantom.sandbox.backends.docker import DEFAULT_IMAGE, DockerBackend


def _docker_running() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(  # noqa: S603 — test helper
            ["docker", "info"],
            capture_output=True,
            timeout=3,
            check=False,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


docker_available = pytest.mark.skipif(
    not _docker_running(), reason="docker daemon not reachable"
)


class TestDockerMetadata:
    def test_name_and_rank(self):
        b = DockerBackend()
        assert b.name == "docker"
        assert b.tier_rank == 4

    def test_default_image_is_alpine(self):
        # The default image must be small and POSIX-compatible. Alpine
        # is the conventional choice; if we ever change it the test
        # forces a conscious decision.
        assert DEFAULT_IMAGE.startswith("alpine:")

    @docker_available
    def test_probe_true(self):
        assert DockerBackend().probe() is True

    def test_probe_false_when_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert DockerBackend().probe() is False
