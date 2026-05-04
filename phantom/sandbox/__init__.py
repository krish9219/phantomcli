"""Phantom sandbox — tiered process isolation for shell and tool execution.

ADR-0003 specifies a four-tier fallback chain:

    bubblewrap (bwrap)  →  firejail  →  unshare + prlimit  →  docker

Each tier implements the same :class:`SandboxBackend` interface. The
:func:`select_backend` function probes the host at startup and picks the
strongest available tier; operators can pin or disable tiers via
``~/.phantom/config.json`` or the ``PHANTOM_SANDBOX_TIER`` env var.

Public entry points
-------------------

:func:`run` — the high-level "execute this command in a sandbox" call.
    Blocks until the command finishes (or its deadline expires) and returns
    a :class:`SandboxResult`. Every shell-style tool in Phantom routes
    through this function; the in-process trust gate from v3 stays in
    place as a *second* defence inside the sandbox.

:class:`SandboxPolicy` — declarative policy object (filesystem mounts,
    network on/off, resource limits, env-var allow-list). Constructed by
    callers from configuration; passed by value into :func:`run`.

:class:`SandboxResult` — the return value of :func:`run`. Includes stdout,
    stderr, exit code, the chosen tier, the wall-clock duration, and a
    ``truncated`` flag when stdout/stderr were capped.

This module is the **only** place in :mod:`phantom` that may call
``subprocess.run``, ``Popen``, ``os.execvp``, or ``os.system``. Every
other module that wants to run a process imports :func:`run` from here.
The Stage-1 smoke test ``tests/sandbox/test_no_unsandboxed_subprocess.py``
enforces that rule by static analysis.

Examples
--------

>>> import os, tempfile
>>> from phantom.sandbox import run, SandboxPolicy
>>> with tempfile.TemporaryDirectory() as d:
...     policy = SandboxPolicy(workdir=d, writable_paths=(d,))
...     result = run(["echo", "hello"], policy)
...     result.exit_code
0
>>> result.stdout.startswith("hello")
True
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from phantom.errors import (
    SandboxBlockedError,
    SandboxError,
    SandboxLaunchError,
    SandboxOutputTruncatedError,
    SandboxResourceError,
    SandboxTimeoutError,
    SandboxUnavailableError,
)
from phantom.sandbox._backend import SandboxBackend
from phantom.sandbox.audit import (
    AuditRecord,
    AuditWriter,
    default_audit_path,
    make_record,
)
from phantom.sandbox.policy import (
    DEFAULT_DENY_PATHS,
    ResourceLimits,
    SandboxPolicy,
)
from phantom.sandbox.result import SandboxResult
from phantom.sandbox.select import (
    PHANTOM_SANDBOX_TIER_ENV,
    available_backends,
    clear_cache,
    select_backend,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from pathlib import Path

__all__ = [
    "DEFAULT_DENY_PATHS",
    "PHANTOM_SANDBOX_TIER_ENV",
    "AuditRecord",
    "AuditWriter",
    "ResourceLimits",
    "SandboxBackend",
    "SandboxBlockedError",
    "SandboxError",
    "SandboxLaunchError",
    "SandboxOutputTruncatedError",
    "SandboxPolicy",
    "SandboxResourceError",
    "SandboxResult",
    "SandboxTimeoutError",
    "SandboxUnavailableError",
    "available_backends",
    "clear_cache_for_tests",
    "default_audit_path",
    "make_record",
    "run",
    "select_backend",
]


def clear_cache_for_tests() -> None:
    """Public alias for :func:`phantom.sandbox.select.clear_cache`.

    Provided so test files don't import from a module with a leading
    underscore in spirit (``select`` is public; ``clear_cache`` was
    deliberately given a longer name to mark it as test-facing).
    """
    clear_cache()


def run(
    argv: list[str],
    policy: SandboxPolicy,
    *,
    backend: SandboxBackend | None = None,
    audit_path: "str | Path | None" = None,
) -> SandboxResult:
    """Execute *argv* under *policy* in a sandbox.

    Parameters
    ----------
    argv:
        Tokenised command. The first element is the program name; subsequent
        elements are passed verbatim. Empty argv raises
        :class:`SandboxLaunchError`.
    policy:
        :class:`SandboxPolicy` instance. See its docstring for fields.
    backend:
        If supplied, use this backend explicitly. Otherwise the result of
        :func:`select_backend` is used. Tests pass an explicit backend to
        cover specific implementations.
    audit_path:
        Override the audit log path. Tests use this to point at a temp file.

    Returns
    -------
    SandboxResult
        Captured stdout/stderr, exit code, duration, chosen tier name, and
        truncation flag.

    Raises
    ------
    SandboxUnavailableError
        No backend is available on this host.
    SandboxLaunchError
        The chosen backend exec'd but failed before the command ran.
    SandboxTimeoutError
        Wall-clock deadline exceeded.
    SandboxOutputTruncatedError
        Only when ``policy.raise_on_truncation`` is True and output exceeded
        a cap.

    Notes
    -----
    The audit-log entry is written **after** the call completes, regardless
    of outcome. A timeout, a launch failure, and a successful return all
    produce exactly one audit record (with appropriate ``code``, ``exit_code``,
    and ``duration_s`` fields).
    """
    if not argv:
        raise SandboxLaunchError("argv is empty")

    chosen = backend if backend is not None else select_backend()
    writer = AuditWriter(audit_path) if audit_path is not None else (
        AuditWriter(default_audit_path()) if policy.capture_audit else None
    )

    started = time.monotonic()
    try:
        result = chosen.launch(argv, policy)
    except SandboxTimeoutError:
        duration = time.monotonic() - started
        if writer is not None:
            writer.write(
                make_record(
                    code="phantom.sandbox.timeout",
                    tier=chosen.name,
                    argv=argv,
                    policy=policy,
                    duration_s=duration,
                    exit_code=None,
                    truncated=False,
                    pid_actual=None,
                )
            )
        raise
    except SandboxOutputTruncatedError:
        duration = time.monotonic() - started
        if writer is not None:
            writer.write(
                make_record(
                    code="phantom.sandbox.output_truncated",
                    tier=chosen.name,
                    argv=argv,
                    policy=policy,
                    duration_s=duration,
                    exit_code=None,
                    truncated=True,
                    pid_actual=None,
                )
            )
        raise
    except SandboxLaunchError:
        duration = time.monotonic() - started
        if writer is not None:
            writer.write(
                make_record(
                    code="phantom.sandbox.launch",
                    tier=chosen.name,
                    argv=argv,
                    policy=policy,
                    duration_s=duration,
                    exit_code=None,
                    truncated=False,
                    pid_actual=None,
                )
            )
        raise
    except SandboxError:
        duration = time.monotonic() - started
        if writer is not None:
            writer.write(
                make_record(
                    code="phantom.sandbox",
                    tier=chosen.name,
                    argv=argv,
                    policy=policy,
                    duration_s=duration,
                    exit_code=None,
                    truncated=False,
                    pid_actual=None,
                )
            )
        raise

    if writer is not None:
        writer.write(
            make_record(
                code="ok" if result.ok else "phantom.sandbox.nonzero_exit",
                tier=chosen.name,
                argv=argv,
                policy=policy,
                duration_s=result.wall_s,
                exit_code=result.exit_code,
                truncated=result.truncated,
                pid_actual=None,
            )
        )
    return result
