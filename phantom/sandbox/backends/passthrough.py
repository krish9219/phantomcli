"""Passthrough backend — no-isolation fallback.

Used on platforms where Phantom doesn't yet ship a real sandbox
(Windows in v1.0). Runs commands in a subprocess **without isolation**
but with three honest defences still in place:

1. **Loud audit log entry** marks every call as ``backend=passthrough``
   so operators see the lack of isolation in their logs.
2. **Resource limits** (wall_s timeout, output truncation) — these are
   process-level controls that don't need a sandbox.
3. **A startup warning** is emitted the first time this backend is
   selected on Windows so operators are reminded they're running
   without isolation.

This backend is **never selected on POSIX** — bwrap/firejail/unshare/
docker are always preferred. It exists purely so Phantom runs at all
on Windows. True Windows sandboxing (AppContainer / Hyper-V isolation)
lands in v1.2.

Tier rank
---------

Set to 99 (lower than every other backend) so the selector chooses any
real backend before this one.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Final

from phantom.errors import SandboxLaunchError, SandboxTimeoutError
from phantom.sandbox._backend import SandboxBackend
from phantom.sandbox.policy import SandboxPolicy
from phantom.sandbox.result import SandboxResult

__all__ = ["PassthroughBackend"]

log = logging.getLogger("phantom.sandbox.passthrough")


# One-shot warning: only emitted the first time the backend is used.
_WARNED_ONCE = False
_WARN_LOCK = threading.Lock()


def _warn_once() -> None:
    global _WARNED_ONCE
    with _WARN_LOCK:
        if _WARNED_ONCE:
            return
        _WARNED_ONCE = True
    log.warning(
        "Phantom sandbox is running in PASSTHROUGH mode (no isolation). "
        "This is the v1.0 fallback for hosts without bwrap/firejail/unshare/"
        "docker — typically Windows. True isolation on Windows lands in "
        "v1.2 via AppContainer. Until then, every shell call runs with "
        "your full user privileges. Do not enable Trust Level 4 (God Mode) "
        "on a passthrough host."
    )


class PassthroughBackend(SandboxBackend):
    """No-isolation backend. Last-resort fallback (Windows + extreme cases)."""

    name: Final[str] = "passthrough"
    tier_rank: Final[int] = 99  # always last

    def probe(self) -> bool:
        # Only "available" on Windows — we never want to silently degrade
        # to passthrough on a Linux box where bwrap-or-friends should
        # have been installed. Operators who actually want passthrough
        # on POSIX (CI, containers without privileges) set the env var
        # PHANTOM_ALLOW_PASSTHROUGH=1.
        if sys.platform == "win32":
            return True
        return os.environ.get("PHANTOM_ALLOW_PASSTHROUGH") == "1"

    def launch(self, argv: list[str], policy: SandboxPolicy) -> SandboxResult:
        _warn_once()
        if not argv:
            raise SandboxLaunchError("empty argv")
        env = self._build_env(policy)
        wall_s = policy.limits.wall_s
        start = time.monotonic()
        cmd_str = " ".join(str(a) for a in argv)
        try:
            proc = subprocess.run(
                argv,
                cwd=policy.workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=wall_s,
            )
        except subprocess.TimeoutExpired as e:
            raise SandboxTimeoutError(cmd_str, deadline_s=wall_s) from e
        except (FileNotFoundError, PermissionError, OSError) as e:
            raise SandboxLaunchError(f"{type(e).__name__}: {e}") from e

        wall_s = round(time.monotonic() - start, 4)
        stdout, stdout_truncated = self._truncate(proc.stdout)
        stderr, stderr_truncated = self._truncate(proc.stderr)
        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            wall_s=wall_s,
            tier=self.name,
            truncated=stdout_truncated or stderr_truncated,
        )

    @staticmethod
    def _build_env(policy: SandboxPolicy) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, v in policy.env.items():
            if v is None:
                inherited = os.environ.get(k)
                if inherited is not None:
                    out[k] = inherited
            else:
                out[k] = v
        # Always expose PATH if the operator didn't set one — Windows
        # in particular can't find anything without it.
        if "PATH" not in out and "PATH" in os.environ:
            out["PATH"] = os.environ["PATH"]
        # On Windows we also need SystemRoot for many tools to work.
        if sys.platform == "win32":
            for must_keep in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP",
                              "LOCALAPPDATA", "APPDATA", "USERPROFILE"):
                if must_keep not in out and must_keep in os.environ:
                    out[must_keep] = os.environ[must_keep]
        return out

    @staticmethod
    def _truncate(text: str, *, cap: int = 1_048_576) -> tuple[str, bool]:
        if len(text) <= cap:
            return text, False
        return text[:cap], True
