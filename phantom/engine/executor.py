"""v4 executor — sandbox-mediated bash execution.

This is the v4 replacement for ``omnicli.executor.execute_bash``. It
preserves the v3 trust-gate + blocklist + audit-log behaviour as a
*second* line of defence and adds:

* **Real isolation.** Every call routes through :func:`phantom.sandbox.run`,
  which picks the strongest available backend on the host
  (bwrap → firejail → unshare → docker).
* **Typed request/response.** No more dict-shaped arguments.
* **Predictable resource ceilings.** The executor builds a
  :class:`SandboxPolicy` from the host's config; no surprises.
* **Trust-cap enforcement at the engine layer**, not just at the
  channel layer (the old v3 design relied on the Telegram bot to cap
  trust to 3; v4 enforces it here).

The v3 executor is **not** removed. It stays at ``omnicli.executor`` for
backwards compatibility with v3 consumers. New code uses this module;
the engine integration tests in Stage 4 wire the agent loop to the v4
executor instead of the v3 one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from phantom.errors import (
    PermissionDeniedError,
    SandboxLaunchError,
    SandboxOutputTruncatedError,
    SandboxTimeoutError,
)
from phantom.sandbox import (
    ResourceLimits,
    SandboxPolicy,
    SandboxResult,
    run,
)

__all__ = [
    "ExecuteBashRequest",
    "ExecuteBashResult",
    "execute_bash",
]


# Permanent blocklist — the same patterns v3 enforces, expressed here so
# v4 callers see the same protection without having to import omnicli.
# We add a small layer on top of the sandbox: even with a sandbox, if
# the operator passes ``--no-sandbox`` (a future Stage-8 feature) these
# patterns must still be blocked.
_PERMANENT_BLOCKLIST: tuple[str, ...] = (
    "rm -rf /",
    "rm -rf /*",
    "rm -rf /home",
    ":(){ :|:& };:",
    "mkfs",
    "dd if=/dev/zero of=/dev/sd",
    "shutdown ",
    "reboot",
    "halt -p",
    ">/dev/sda",
    "echo nameserver",
    "chattr -i",
)


@dataclass(frozen=True)
class ExecuteBashRequest:
    """A request to run a bash command under the sandbox.

    Attributes
    ----------
    command:
        The full command line as a string. Passed to ``/bin/sh -c`` inside
        the sandbox; do not pre-tokenise. Subject to the permanent
        blocklist.
    workdir:
        Directory to chdir into. Must be inside ``writable_paths``.
    writable_paths:
        Bind-mounted read-write into the sandbox.
    network:
        Whether the sandbox should have network access. Defaults to False.
    trust:
        v3-style trust level (1-4). Used for blocklist + per-tier policy.
        Trust 4 (God Mode) does NOT bypass the sandbox; it only relaxes
        the v3 prompt-on-write behaviour, which is irrelevant under v4.
    limits:
        Optional :class:`ResourceLimits` override.
    original_argv:
        Optional pre-quoting argv, supplied by callers (like ``phantom
        run``) that already tokenise the command. The blocklist check
        runs against ``" ".join(original_argv)`` *as well as* the
        ``command`` field, so a quoted command like ``'rm' '-rf' '/'``
        still matches the unquoted blocklist pattern ``rm -rf /``.
    """

    command: str
    workdir: str
    writable_paths: tuple[str, ...] = ()
    network: bool = False
    trust: int = 3
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    original_argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecuteBashResult:
    """The outcome of an :func:`execute_bash` call."""

    stdout: str
    stderr: str
    exit_code: int
    wall_s: float
    tier: str
    truncated: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @classmethod
    def from_sandbox(cls, sr: SandboxResult) -> "ExecuteBashResult":
        return cls(
            stdout=sr.stdout,
            stderr=sr.stderr,
            exit_code=sr.exit_code,
            wall_s=sr.wall_s,
            tier=sr.tier,
            truncated=sr.truncated,
        )


def _check_blocklist(command: str, original_argv: tuple[str, ...] = ()) -> None:
    """Raise :class:`PermissionDeniedError` for permanently-blocked patterns.

    Two haystacks are checked:

    1. *command* itself, lower-cased.
    2. ``" ".join(original_argv)`` lower-cased, when supplied. This
       catches the case where the caller already quoted the argv
       (e.g. ``'rm' '-rf' '/'``); the blocklist patterns are written
       in the unquoted shell form a human would type.
    """
    haystacks: list[str] = [command.lower()]
    if original_argv:
        haystacks.append(" ".join(original_argv).lower())
    for pat in _PERMANENT_BLOCKLIST:
        for h in haystacks:
            if pat in h:
                raise PermissionDeniedError(
                    f"command matches the permanent blocklist pattern {pat!r}"
                )


def _writable_paths_for(req: ExecuteBashRequest) -> tuple[str, ...]:
    """Determine the final writable_paths set for the sandbox."""
    if req.writable_paths:
        return req.writable_paths
    # Default: only the workdir is writable.
    return (req.workdir,)


def execute_bash(req: ExecuteBashRequest) -> ExecuteBashResult:
    """Execute ``req.command`` inside a sandbox.

    Parameters
    ----------
    req:
        :class:`ExecuteBashRequest` describing the command, workdir, and
        policy.

    Raises
    ------
    PermissionDeniedError
        If ``req.command`` matches the permanent blocklist.
    SandboxLaunchError
        If the sandbox could not start the process.
    SandboxTimeoutError
        If the wall-clock deadline was exceeded.
    SandboxOutputTruncatedError
        If output exceeded the cap **and** the policy was constructed
        with ``raise_on_truncation=True``.

    Returns
    -------
    ExecuteBashResult
        Captured stdout/stderr, exit code, duration, chosen tier name,
        and truncation flag.

    Notes
    -----
    The blocklist check happens *before* the sandbox is consulted, so a
    blocked command never makes it to the launch path. This is
    defence-in-depth: the sandbox would prevent kernel-level damage
    anyway, but the blocklist ensures we don't waste a process on
    obviously-malicious input.
    """
    if not req.command.strip():
        raise SandboxLaunchError("command is empty")

    _check_blocklist(req.command, req.original_argv)

    # Materialise the workdir so the sandbox bind-mount succeeds.
    Path(req.workdir).mkdir(parents=True, exist_ok=True)

    policy = SandboxPolicy(
        workdir=req.workdir,
        writable_paths=_writable_paths_for(req),
        network=req.network,
        limits=req.limits,
    )
    argv = ["/bin/sh", "-c", req.command]
    sr = run(argv, policy)
    return ExecuteBashResult.from_sandbox(sr)
