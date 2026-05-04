"""Sandbox audit log — append-only record of every sandboxed call.

Every :func:`phantom.sandbox.run` invocation that completes (success or
failure) writes one JSON record to ``~/.phantom/sandbox-audit.log``. The
log is:

* **Append-only.** We never truncate. Operators rotate it themselves;
  the recommended cadence is logrotate weekly with 14-day retention.
* **Mode 0600.** Owner-readable, owner-writable, nothing else. Set on
  every write because some tooling resets file modes.
* **One record per line** (newline-delimited JSON). Tail-friendly; the
  Stage-8 dashboard streams it directly.
* **Atomic.** Each record is composed in memory and written with one
  ``write`` call to a file opened in O_APPEND mode. The kernel guarantees
  per-write atomicity for writes ≤ PIPE_BUF (4 KiB on Linux); records
  above that size are rare in practice (we hash command lines, we don't
  store them) but the buffer is flushed before close to avoid loss on
  power failure.

Record schema (stable across releases):

* ``ts``           — ISO-8601 timestamp (UTC, microsecond precision).
* ``code``         — short outcome identifier (``"ok"``, or one of the
  :class:`phantom.errors.SandboxError` ``code`` values).
* ``tier``         — backend name that ran the command.
* ``cmd_sha256``   — SHA-256 hex digest of the argv joined by ``\\x00``.
* ``argv_len``     — number of argv tokens (the digest's pre-image).
* ``policy_hash``  — short hash of the policy (mounts, network, limits).
* ``deadline_s``   — wall-clock deadline used for this call.
* ``duration_s``   — wall-clock duration (rounded to 4 decimal places).
* ``exit_code``    — process exit code, or ``null`` on launch failure.
* ``truncated``    — bool, whether stdout/stderr were capped.
* ``pid_actual``   — host PID that ran the command (debugging aid).
* ``phantom_ver``  — phantom version string.

We intentionally do **not** log the command-line text or stdout/stderr.
Those are private to the operator and would turn the audit log into a
secrets-exfiltration target. The hash + argv length is enough to confirm
"two calls ran the same command".
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phantom._version import __version__
from phantom.sandbox.policy import SandboxPolicy

__all__ = ["AuditRecord", "AuditWriter", "default_audit_path"]


def default_audit_path() -> Path:
    """Return the canonical audit-log path: ``~/.phantom/sandbox-audit.log``.

    Honours ``$PHANTOM_HOME`` for tests. Creates the parent directory with
    mode 0700 if it does not exist; never raises if the directory exists.
    """
    base = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p / "sandbox-audit.log"


def _hash_argv(argv: list[str]) -> str:
    payload = b"\x00".join(s.encode("utf-8", errors="replace") for s in argv)
    return hashlib.sha256(payload).hexdigest()


def _hash_policy(policy: SandboxPolicy) -> str:
    """Short, stable digest of the security-relevant policy fields.

    Two policies that produce different digests have at least one
    security-relevant difference. Field ordering is fixed so that
    re-ordering writable_paths in source code does not change the hash.
    """
    parts = (
        f"net={int(policy.network)}",
        "rw=" + ",".join(sorted(policy.writable_paths)),
        "ro=" + ",".join(sorted(policy.read_only_paths)),
        "deny=" + ",".join(sorted(policy.deny_paths)),
        f"wall={policy.limits.wall_s:.3f}",
        f"cpu={'-' if policy.limits.cpu_s is None else f'{policy.limits.cpu_s:.3f}'}",
        f"rss={'-' if policy.limits.rss_mib is None else policy.limits.rss_mib}",
        f"fds={'-' if policy.limits.fds is None else policy.limits.fds}",
        f"out={policy.limits.stdout_bytes}",
        f"err={policy.limits.stderr_bytes}",
    )
    blob = "|".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One row of the audit log."""

    ts: str
    code: str
    tier: str
    cmd_sha256: str
    argv_len: int
    policy_hash: str
    deadline_s: float
    duration_s: float
    exit_code: int | None
    truncated: bool
    pid_actual: int | None
    phantom_ver: str

    def to_json_line(self) -> str:
        """Render a single newline-terminated JSON line."""
        d: dict[str, Any] = {
            "ts": self.ts,
            "code": self.code,
            "tier": self.tier,
            "cmd_sha256": self.cmd_sha256,
            "argv_len": self.argv_len,
            "policy_hash": self.policy_hash,
            "deadline_s": self.deadline_s,
            "duration_s": self.duration_s,
            "exit_code": self.exit_code,
            "truncated": self.truncated,
            "pid_actual": self.pid_actual,
            "phantom_ver": self.phantom_ver,
        }
        return json.dumps(d, separators=(",", ":")) + "\n"


class AuditWriter:
    """Append-only audit-log writer.

    Each instance owns a path. Calls to :meth:`write` are independently
    atomic at the file level (one ``write`` syscall per record). Multiple
    :class:`AuditWriter` instances pointing at the same path are safe to
    interleave because every open uses ``O_APPEND``.

    Examples
    --------

    >>> import tempfile, os
    >>> with tempfile.TemporaryDirectory() as d:
    ...     w = AuditWriter(os.path.join(d, "audit.log"))
    ...     n = w.write(_demo_record())
    ...     n > 0
    True
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        # Ensure the parent directory exists; do not change its mode if it
        # already does.
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @property
    def path(self) -> Path:
        return self._path

    def write(self, rec: AuditRecord) -> int:
        """Append *rec* to the audit log. Returns the number of bytes written."""
        line = rec.to_json_line().encode("utf-8")
        # O_APPEND guarantees the kernel does the seek-and-write as a single
        # operation, so concurrent writers cannot interleave a record.
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        fd = os.open(self._path, flags, mode=0o600)
        try:
            written = os.write(fd, line)
        finally:
            os.close(fd)
        # Re-stat and chmod if the mode is wider than 0600 (e.g. operator
        # touched the file with a wider umask). Cheap; runs once per write.
        try:
            current = stat.S_IMODE(self._path.stat().st_mode)
            if current != 0o600:
                os.chmod(self._path, 0o600)
        except FileNotFoundError:  # pragma: no cover — race with rotation
            pass
        return written


def make_record(
    *,
    code: str,
    tier: str,
    argv: list[str],
    policy: SandboxPolicy,
    duration_s: float,
    exit_code: int | None,
    truncated: bool,
    pid_actual: int | None,
) -> AuditRecord:
    """Construct an :class:`AuditRecord` from the inputs of one call.

    Centralised so backends can call it with their own observed duration
    and exit code; the formatting (timestamp, hashes) is consistent across
    backends.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return AuditRecord(
        ts=ts,
        code=code,
        tier=tier,
        cmd_sha256=_hash_argv(argv),
        argv_len=len(argv),
        policy_hash=_hash_policy(policy),
        deadline_s=policy.limits.wall_s,
        duration_s=round(duration_s, 4),
        exit_code=exit_code,
        truncated=truncated,
        pid_actual=pid_actual,
        phantom_ver=__version__,
    )


def _demo_record() -> AuditRecord:  # pragma: no cover — doctest helper only
    """Return a minimal AuditRecord for the module docstring's doctest."""
    return AuditRecord(
        ts=datetime.now(timezone.utc).isoformat(),
        code="ok",
        tier="unshare",
        cmd_sha256="0" * 64,
        argv_len=2,
        policy_hash="abc123",
        deadline_s=30.0,
        duration_s=0.01,
        exit_code=0,
        truncated=False,
        pid_actual=int(time.time()) & 0xFFFF,
        phantom_ver=__version__,
    )
