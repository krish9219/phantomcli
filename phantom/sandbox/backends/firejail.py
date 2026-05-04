"""``firejail`` backend.

firejail is the older-but-still-supported alternative to bwrap.
Distribution coverage: present in apt and dnf on every mainstream distro,
sometimes more reliably than bwrap on hardened kernels (Debian's
``unprivileged_userns_clone`` defaults differ across versions). ADR-0003
ranks it second.

Differences from bwrap
----------------------

* firejail's ``--private`` and ``--whitelist`` switches give us the
  filesystem deny-list functionality, but with different syntax. We
  translate :class:`SandboxPolicy` accordingly.
* firejail does its own seccomp filtering by default; we leave it on
  with ``--seccomp``.
* firejail does not provide a fresh ``/tmp`` automatically the way bwrap
  does; we use ``--private-tmp``.
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
from phantom.sandbox.limits import prlimit_args
from phantom.sandbox.policy import SandboxPolicy
from phantom.sandbox.result import SandboxResult

__all__ = ["FirejailBackend"]


class FirejailBackend(SandboxBackend):
    """``firejail``-based sandbox."""

    name: ClassVar[str] = "firejail"  # type: ignore[misc]
    tier_rank: ClassVar[int] = 2  # type: ignore[misc]

    def probe(self) -> bool:
        if shutil.which("firejail") is None:
            return False
        try:
            r = subprocess.run(  # noqa: S603 — sandbox backend, allowed
                ["firejail", "--version"],
                capture_output=True,
                timeout=2,
                check=False,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def launch(self, argv: list[str], policy: SandboxPolicy) -> SandboxResult:
        if not argv:
            raise SandboxLaunchError("argv is empty")

        fj_args: list[str] = ["firejail", "--quiet", "--noprofile"]
        # Capabilities — drop everything.
        fj_args += ["--caps.drop=all"]
        # seccomp default filter
        fj_args += ["--seccomp"]
        # Hostname
        fj_args += ["--hostname=phantom-sandbox"]
        # Network
        if not policy.network:
            fj_args.append("--net=none")

        # Whitelist the writable paths. firejail's whitelist makes a
        # host path visible inside an otherwise-private fs; we use it
        # so the tmpfs at /tmp doesn't shadow user workdirs that
        # happen to live under /tmp.
        for wp in policy.writable_paths:
            if not os.path.exists(wp):
                try:
                    os.makedirs(wp, exist_ok=True, mode=0o700)
                except OSError as exc:
                    raise SandboxLaunchError(
                        f"writable_path {wp!r} could not be created: {exc}"
                    ) from exc
            fj_args.append(f"--whitelist={wp}")

        # Filesystem deny-list. Skip paths that overlap a writable_path.
        wp_norm = tuple(p.rstrip("/") for p in policy.writable_paths)
        for dp in policy.expanded_deny_paths(home=os.path.expanduser("~")):
            if not os.path.exists(dp):
                continue
            d_norm = dp.rstrip("/")
            if any(d_norm == w or d_norm.startswith(w + "/") for w in wp_norm):
                continue
            fj_args.append(f"--blacklist={dp}")

        # Read-only paths
        for ro in policy.read_only_paths:
            if os.path.exists(ro):
                fj_args.append(f"--read-only={ro}")

        # firejail's chdir support varies across versions (--chdir is
        # not in 0.9.80; --private-cwd is restricted). We wrap the
        # command in a shell that does the chdir itself, matching the
        # unshare backend's pattern.
        prefix = prlimit_args(policy.limits)
        sh_command = 'cd "$WORKDIR" && exec "$@"'
        full_argv = [
            *fj_args, "--",
            *prefix, "--",
            "/bin/sh", "-c", sh_command, "phantom-sandbox", *argv,
        ]
        env = self._build_env(policy)
        env["WORKDIR"] = policy.workdir

        return self._run(full_argv, env, policy)

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
    def _run(
        full_argv: list[str], env: dict[str, str], policy: SandboxPolicy
    ) -> SandboxResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 — sandbox backend, allowed
                full_argv,
                env=env,
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
            raise SandboxLaunchError(f"firejail not on PATH: {exc}") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxLaunchError(f"firejail backend failed: {exc}") from exc

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
            tier="firejail",
            truncated=truncated,
        )
