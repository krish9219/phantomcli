"""``bubblewrap`` backend — preferred Linux sandbox.

bwrap is the lightweight isolator behind Flatpak. It's small (single
binary), fast (sub-50 ms cold start), and gives us strict filesystem
control via explicit bind-mounts. ADR-0003 ranks it first.

What this backend does
----------------------

* Creates user/PID/mount/UTS/IPC/cgroup namespaces.
* Optionally creates a network namespace (when policy.network is False).
* Bind-mounts the policy's read-only paths read-only.
* Bind-mounts the policy's writable paths read-write.
* Mounts a fresh tmpfs at ``/tmp`` (always — bwrap has direct support).
* Hides the policy's deny-list paths by bind-mounting an empty tmpfs
  over them.
* Drops every Linux capability that bwrap can drop (``--cap-drop ALL``).
* Routes through ``prlimit`` for resource enforcement.

The translation between :class:`SandboxPolicy` and bwrap's argv is
mechanical; the test suite ``tests/sandbox/test_bwrap.py`` asserts the
exact flag layout for representative policies.
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

__all__ = ["BwrapBackend"]


class BwrapBackend(SandboxBackend):
    """``bwrap``-based sandbox."""

    name: ClassVar[str] = "bwrap"  # type: ignore[misc]
    tier_rank: ClassVar[int] = 1  # type: ignore[misc]

    def probe(self) -> bool:
        if shutil.which("bwrap") is None:
            return False
        try:
            r = subprocess.run(  # noqa: S603 — sandbox backend, allowed
                ["bwrap", "--version"],
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

        bwrap_args: list[str] = ["bwrap"]
        # Namespaces
        bwrap_args += ["--unshare-user-try", "--unshare-pid", "--unshare-ipc",
                       "--unshare-uts", "--unshare-cgroup-try"]
        if not policy.network:
            bwrap_args.append("--unshare-net")

        # Drop all capabilities bwrap can drop. The "--cap-drop ALL" form
        # requires bwrap to have CAP_SETPCAP, which it does in the
        # default install.
        bwrap_args += ["--cap-drop", "ALL"]

        # Hostname
        bwrap_args += ["--hostname", "phantom-sandbox"]

        # Filesystem layout. bwrap requires explicit mounts; a sandboxed
        # process sees only what we mount.
        #
        # Order matters in bwrap: later mounts can shadow earlier ones.
        # We apply mounts in this order, top-down:
        #   1. tmpfs /tmp     (so writable_paths under /tmp can drill into it)
        #   2. proc and dev
        #   3. read-only system paths
        #   4. writable paths (last so they override anything above)
        #   5. deny-list paths (only over things outside writable_paths)

        # /tmp: fresh tmpfs every call. Mounted FIRST so writable bind
        # mounts under /tmp drill through to the host's actual path
        # rather than being shadowed by a later --tmpfs.
        bwrap_args += ["--tmpfs", "/tmp"]
        # /proc: bwrap's --proc gives an isolated /proc rooted at the
        # sandbox's PID namespace.
        bwrap_args += ["--proc", "/proc"]
        # /dev: minimal — /dev/null, /dev/zero, /dev/random, /dev/urandom,
        # /dev/tty, /dev/full, /dev/ptmx.
        bwrap_args += ["--dev", "/dev"]
        # /sys: read-only bind from the host so tools that read sysfs
        # (e.g. ip, lsblk, cgroup-aware tooling) work. Inside an isolated
        # network namespace, /sys/class/net only contains lo until the
        # operator brings it up. With network=True the sandbox shares the
        # host's net namespace and sees host interfaces.
        if os.path.exists("/sys"):
            bwrap_args += ["--ro-bind", "/sys", "/sys"]

        # Read-only system roots
        for ro in policy.read_only_paths:
            if os.path.exists(ro):
                bwrap_args += ["--ro-bind", ro, ro]

        # Writable paths — applied after the system mounts so they win
        # in case of overlap.
        for wp in policy.writable_paths:
            if os.path.exists(wp):
                bwrap_args += ["--bind", wp, wp]
            else:
                # bwrap requires the source to exist; if the operator
                # asked for a writable path that doesn't exist on the
                # host, create it (best effort).
                try:
                    os.makedirs(wp, exist_ok=True, mode=0o700)
                    bwrap_args += ["--bind", wp, wp]
                except OSError as exc:
                    raise SandboxLaunchError(
                        f"writable_path {wp!r} does not exist and could not be created: {exc}"
                    ) from exc

        # Hide denied paths. bwrap distinguishes between files and
        # directories. We skip any deny path that overlaps a writable_path
        # — the operator explicitly asked for write access there, and a
        # /dev/null bind would corrupt their workspace.
        wp_norm = tuple(p.rstrip("/") for p in policy.writable_paths)
        for dp in policy.expanded_deny_paths(home=os.path.expanduser("~")):
            if not os.path.exists(dp):
                continue
            dp_norm = dp.rstrip("/")
            if any(dp_norm == w or dp_norm.startswith(w + "/") for w in wp_norm):
                continue
            if os.path.isdir(dp):
                bwrap_args += ["--tmpfs", dp]
            else:
                # File or special node — hide via /dev/null bind.
                bwrap_args += ["--ro-bind", "/dev/null", dp]

        # chdir into the workdir (bwrap supports --chdir directly).
        bwrap_args += ["--chdir", policy.workdir]

        # Clear environment, then re-build it from the policy.
        bwrap_args.append("--clearenv")
        env_for_bwrap: list[str] = []
        env = self._build_env(policy)
        for k, v in env.items():
            bwrap_args += ["--setenv", k, v]
            env_for_bwrap.append(f"{k}={v}")

        # Resource limits via prlimit wrapper.
        prefix = prlimit_args(policy.limits)

        full_argv = [*bwrap_args, "--", *prefix, "--", *argv]
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
            raise SandboxLaunchError(f"bwrap not on PATH: {exc}") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxLaunchError(f"bwrap backend failed: {exc}") from exc

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
            tier="bwrap",
            truncated=truncated,
        )
