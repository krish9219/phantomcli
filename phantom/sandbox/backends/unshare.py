"""``unshare`` backend — pure-kernel namespaces + prlimit fallback.

Always available on Linux ≥ 3.8 with user namespaces enabled (Linux 5.x+
distributions ship this on by default). No daemon, no extra packages —
``unshare`` and ``prlimit`` are part of util-linux and live on every
mainstream distro.

Isolation envelope
------------------

* **PID namespace** (``--pid``) — sandboxed process can only see itself
  and its descendants.
* **Mount namespace** (``--mount``) — bind-mount manipulations stay
  inside the sandbox.
* **UTS namespace** (``--uts``) — sandbox sees its own hostname.
* **IPC namespace** (``--ipc``) — System V IPC is isolated.
* **Network namespace** (``--net``) — applied iff ``policy.network``
  is False. Disconnects the sandboxed process from every host
  interface; only loopback exists inside, and even loopback starts down.
* **User namespace** (``--user --map-root-user``) — sandboxed process
  becomes uid 0 inside the namespace, mapped to the host's real uid.
  Lets us mount-bind without privileges.

Limitations relative to bwrap/firejail
--------------------------------------

* No filesystem deny-list enforcement at the mount layer (we don't
  re-mount anything; the sandboxed process sees the original host
  filesystem). The ADR-0003 deny-list is enforced at the *bwrap* tier;
  on the unshare tier, the ``ResourceLimits`` and the v3 trust gate are
  the protections.
* No seccomp filter — pure-Python seccomp is out of scope (ADR-0003);
  the kernel's namespace boundary is the protection.

This backend is the *kernel-only* lower bound for what every Linux host
can run. Operators who want strict filesystem isolation install bwrap.
"""

from __future__ import annotations

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
from phantom.sandbox.limits import prlimit_args
from phantom.sandbox.policy import SandboxPolicy
from phantom.sandbox.result import SandboxResult

__all__ = ["UnshareBackend"]


class UnshareBackend(SandboxBackend):
    """``unshare``-based sandbox. Linux-only, kernel-only."""

    name: ClassVar[str] = "unshare"  # type: ignore[misc]
    tier_rank: ClassVar[int] = 3  # type: ignore[misc]

    def probe(self) -> bool:
        """Return True iff ``unshare`` and ``prlimit`` are on PATH and the
        kernel supports the namespaces we want."""
        if shutil.which("unshare") is None:
            return False
        if shutil.which("prlimit") is None:
            return False
        try:
            # The cheapest possible namespace-creation test.
            r = subprocess.run(  # noqa: S603 — sandbox backend, allowed
                ["unshare", "--user", "--mount", "--fork", "true"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def launch(self, argv: list[str], policy: SandboxPolicy) -> SandboxResult:
        if not argv:
            raise SandboxLaunchError("argv is empty")

        # --kill-child=SIGKILL ensures that if our parent process kills
        # `unshare` itself (e.g. subprocess.run timeout SIGKILL), the
        # child inside the namespace is also reaped — without this,
        # PID-namespace orphans can survive briefly and the timeout
        # becomes flaky under load.
        unshare_args = [
            "unshare",
            "--mount", "--uts", "--ipc", "--pid", "--fork",
            "--kill-child=SIGKILL",
        ]
        if not policy.network:
            unshare_args.append("--net")
        # User namespace lets us run as a 'fake root' inside the namespace
        # without the host privilege. Required for the bwrap/unshare
        # comparison to be meaningful.
        unshare_args += ["--user", "--map-root-user"]

        # Working directory: ``unshare`` does not have a --chdir flag in
        # all versions, so we wrap with /bin/sh -c 'cd "$1"; shift; exec
        # "$@"'. This adds a shell process inside the sandbox, which is
        # acceptable.
        prefix = prlimit_args(policy.limits)

        # Compose the full argv:
        #   unshare ... -- prlimit ... -- /bin/sh -c 'cd "$DIR"; exec "$@"' -- <argv>
        sh_command = 'cd "$WORKDIR" && exec "$@"'
        full_argv: list[str] = [
            *unshare_args,
            "--",
            *prefix,
            "--",
            "/bin/sh",
            "-c",
            sh_command,
            "phantom-sandbox",
            *argv,
        ]

        env = self._build_env(policy)
        env["WORKDIR"] = policy.workdir

        return self._run(full_argv, env, policy)

    @staticmethod
    def _build_env(policy: SandboxPolicy) -> dict[str, str]:
        """Build the environment for the sandboxed process from the policy."""
        import os

        env: dict[str, str] = {}
        # Always set a minimal sane PATH so /bin/sh can find common tools.
        # The operator can override.
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
        env["HOME"] = policy.workdir  # not the host's home — defence in depth
        env["LANG"] = "C.UTF-8"
        env["LC_ALL"] = "C.UTF-8"

        # Apply the policy's env allow-list / explicit values.
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
            # Wall-clock deadline was exceeded. Partial output is discarded
            # — the process was forcibly killed and any captured bytes are
            # not guaranteed to be consistent.
            raise SandboxTimeoutError(
                " ".join(full_argv),
                deadline_s=policy.limits.wall_s,
            ) from None
        except FileNotFoundError as exc:
            raise SandboxLaunchError(
                f"unshare not on PATH: {exc}"
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxLaunchError(
                f"unshare backend failed to launch: {exc}"
            ) from exc

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
            tier="unshare",
            truncated=truncated,
        )


def _truncate(s: str, cap_bytes: int) -> tuple[str, bool]:
    """Truncate *s* to *cap_bytes* bytes; return (text, truncated_flag).

    We measure in UTF-8 bytes so the cap matches the policy field's units.
    The truncation marker is a single line appended at the end.
    """
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= cap_bytes:
        return s, False
    # Truncate, leaving room for the marker line.
    marker = b"\n[phantom-sandbox: output truncated]\n"
    head = encoded[: max(0, cap_bytes - len(marker))]
    # Decode again. Use errors='replace' to avoid mid-multibyte breakage.
    return (head + marker).decode("utf-8", errors="replace"), True
