"""``docker`` backend.

Heavyweight, daemon-based, but the only practical option on macOS and on
Windows-WSL when bwrap/firejail/unshare are not available. ADR-0003 ranks
it last but mandates it as the fallback for non-Linux hosts.

Strategy
--------

* Image: ``alpine:3.19`` by default — small (~5 MiB), POSIX-y, has
  ``/bin/sh`` and basic coreutils. Operators can override via the env
  variable ``PHANTOM_SANDBOX_DOCKER_IMAGE``.
* Volume mounts translate :class:`SandboxPolicy.writable_paths` and
  ``read_only_paths`` to ``-v <host>:<host>[:ro]``. Same path on host
  and container — no path translation, matching the other backends.
* Network: ``--network=none`` when policy.network is False.
* Resource limits via ``--ulimit`` and ``--memory`` (see
  :func:`phantom.sandbox.limits.docker_flags`).
* Capabilities: ``--cap-drop=ALL``.
* Read-only root: ``--read-only`` plus an explicit ``--tmpfs /tmp``.
* Auto-remove: ``--rm`` so we never leave dangling containers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import ClassVar

from phantom.errors import (
    SandboxLaunchError,
    SandboxOutputTruncatedError,
    SandboxTimeoutError,
)
from phantom.sandbox._backend import SandboxBackend
from phantom.sandbox.backends.unshare import _truncate
from phantom.sandbox.limits import docker_flags
from phantom.sandbox.policy import SandboxPolicy
from phantom.sandbox.result import SandboxResult

__all__ = ["DockerBackend", "DEFAULT_IMAGE"]

DEFAULT_IMAGE: str = "alpine:3.19"


class DockerBackend(SandboxBackend):
    """``docker``-based sandbox.

    The image is pulled lazily on first use; we do **not** pull during
    :meth:`probe`, because probing must be fast.
    """

    name: ClassVar[str] = "docker"  # type: ignore[misc]
    tier_rank: ClassVar[int] = 4  # type: ignore[misc]

    def probe(self) -> bool:
        if shutil.which("docker") is None:
            return False
        try:
            # Use --format to ask for OSType in one round-trip. This both
            # confirms the daemon is reachable (non-zero rc otherwise) and
            # tells us whether it's Linux- or Windows-container mode.
            # Our launch() builds Linux-only flags (--read-only, --tmpfs,
            # --cap-drop=ALL, alpine image) which Windows containers
            # reject outright, so probe False on Windows-container hosts.
            # Docker Desktop on Windows with the WSL2 backend reports
            # OSType=linux and works fine; the native Windows-container
            # mode reports OSType=windows.
            r = subprocess.run(  # noqa: S603 — sandbox backend, allowed
                ["docker", "info", "--format", "{{.OSType}}"],
                capture_output=True,
                timeout=3,
                check=False,
            )
            if r.returncode != 0:
                return False
            return r.stdout.decode("utf-8", errors="replace").strip().lower() == "linux"
        except (OSError, subprocess.SubprocessError):
            return False

    def launch(self, argv: list[str], policy: SandboxPolicy) -> SandboxResult:
        if not argv:
            raise SandboxLaunchError("argv is empty")

        image = os.environ.get("PHANTOM_SANDBOX_DOCKER_IMAGE", DEFAULT_IMAGE)

        docker_args: list[str] = [
            "docker", "run", "--rm",
            "--read-only", "--tmpfs", "/tmp",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--hostname", "phantom-sandbox",
        ]
        if not policy.network:
            docker_args += ["--network", "none"]
        docker_args += docker_flags(policy.limits)

        # Volume mounts.
        for ro in policy.read_only_paths:
            if os.path.exists(ro):
                docker_args += ["-v", f"{ro}:{ro}:ro"]
        for wp in policy.writable_paths:
            if os.path.exists(wp):
                docker_args += ["-v", f"{wp}:{wp}:rw"]
            else:
                try:
                    os.makedirs(wp, exist_ok=True, mode=0o700)
                    docker_args += ["-v", f"{wp}:{wp}:rw"]
                except OSError as exc:
                    raise SandboxLaunchError(
                        f"writable_path {wp!r} does not exist and could not be created: {exc}"
                    ) from exc

        # Working directory inside the container.
        docker_args += ["-w", policy.workdir]

        # Environment — clean slate, then policy.
        env = self._build_env(policy)
        for k, v in env.items():
            docker_args += ["-e", f"{k}={v}"]

        # Image and command.
        full_argv = [*docker_args, image, *argv]
        return self._run(full_argv, policy)

    @staticmethod
    def _build_env(policy: SandboxPolicy) -> dict[str, str]:
        env: dict[str, str] = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": policy.workdir,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        for key, val in policy.env.items():
            if val is None:
                inherited = os.environ.get(key)
                if inherited is not None:
                    env[key] = inherited
            else:
                env[key] = val
        return env

    @staticmethod
    def _run(full_argv: list[str], policy: SandboxPolicy) -> SandboxResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 — sandbox backend, allowed
                full_argv,
                input=b"",
                capture_output=True,
                timeout=policy.limits.wall_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SandboxTimeoutError(
                " ".join(full_argv),
                deadline_s=policy.limits.wall_s,
            ) from None
        except FileNotFoundError as exc:
            raise SandboxLaunchError(f"docker not on PATH: {exc}") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxLaunchError(f"docker backend failed: {exc}") from exc

        duration = time.monotonic() - start
        stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

        stdout, t1 = _truncate(stdout, policy.limits.stdout_bytes)
        stderr, t2 = _truncate(stderr, policy.limits.stderr_bytes)
        truncated = t1 or t2

        if truncated and policy.raise_on_truncation:
            raise SandboxOutputTruncatedError("output exceeded cap")

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            wall_s=duration,
            tier="docker",
            truncated=truncated,
        )
