"""Phantom's typed exception hierarchy.

Every public function in `phantom.*` either returns a value or raises an
exception derived from :class:`PhantomError`. No bare ``Exception``,
no ``RuntimeError`` from business code. This rule is enforced by the
Stage-1+ smoke tests.

The hierarchy:

::

    PhantomError                       # all phantom-raised errors derive from this
    ├── ConfigError                    # bad / missing configuration
    ├── SandboxError                   # all sandbox-related failures
    │   ├── SandboxUnavailableError    # no backend installed
    │   ├── SandboxLaunchError         # backend exec'd but failed at launch
    │   ├── SandboxResourceError       # CPU / RSS / wall-time / output cap hit
    │   ├── SandboxTimeoutError        # wall-clock deadline exceeded
    │   ├── SandboxBlockedError        # operator policy rejected the call
    │   └── SandboxOutputTruncatedError# stdout/stderr capped (informational)
    ├── PermissionDeniedError          # trust-gate / capability rejection
    ├── PluginError                    # plugin load / activate / call failures
    ├── ChannelError                   # channel adapter problems
    ├── ProtocolError                  # MCP / ACP wire-format issues
    └── LicenseError                   # Pro-tier license validation

Usage:

>>> from phantom.errors import SandboxTimeoutError
>>> try:
...     raise SandboxTimeoutError("ls -R /", deadline_s=5.0)
... except SandboxTimeoutError as exc:
...     exc.command
'ls -R /'
"""

from __future__ import annotations

__all__ = [
    "ChannelError",
    "ConfigError",
    "LicenseError",
    "PermissionDeniedError",
    "PhantomError",
    "PluginError",
    "ProtocolError",
    "SandboxBlockedError",
    "SandboxError",
    "SandboxLaunchError",
    "SandboxOutputTruncatedError",
    "SandboxResourceError",
    "SandboxTimeoutError",
    "SandboxUnavailableError",
]


class PhantomError(Exception):
    """Base class for every error raised by ``phantom.*``.

    Catch this in code that wants to handle "any phantom-internal failure"
    while still letting genuine bugs (KeyError, TypeError) propagate.

    Attributes
    ----------
    detail:
        Operator-readable explanation. Safe to display in the REPL.
    code:
        Machine-readable short identifier; stable across releases. Used by
        the dashboard and the audit log.
    """

    code: str = "phantom.error"

    def __init__(self, detail: str = "", *, code: str | None = None) -> None:
        super().__init__(detail or self.__class__.__name__)
        self.detail = detail
        if code is not None:
            self.code = code


class ConfigError(PhantomError):
    """Misconfiguration. The user must edit `~/.phantom/config.json` to fix."""

    code = "phantom.config"


class PermissionDeniedError(PhantomError):
    """A capability the agent asked for is not granted to it."""

    code = "phantom.permission_denied"


# ─── Sandbox errors ───────────────────────────────────────────────────────────


class SandboxError(PhantomError):
    """Base for every sandbox-related failure."""

    code = "phantom.sandbox"


class SandboxUnavailableError(SandboxError):
    """No sandbox backend is available on this host.

    Stage 1 makes shell tools refuse to run in this state. The user is
    pointed at ``phantom doctor`` for installation instructions.
    """

    code = "phantom.sandbox.unavailable"


class SandboxLaunchError(SandboxError):
    """The chosen backend exec'd but failed before the command ran.

    Examples: ``bwrap`` exited 1 with "permission denied" because the host
    lacks user-namespace support; docker daemon refused the connection.
    """

    code = "phantom.sandbox.launch"


class SandboxResourceError(SandboxError):
    """A resource ceiling (CPU, RSS, FD count, output bytes) was hit.

    ``which`` identifies the dimension that tripped (one of ``"cpu"``,
    ``"rss"``, ``"fds"``, ``"stdout"``, ``"stderr"``).
    """

    code = "phantom.sandbox.resource"

    def __init__(self, detail: str, *, which: str) -> None:
        super().__init__(detail)
        self.which = which


class SandboxTimeoutError(SandboxError):
    """Wall-clock deadline exceeded inside the sandbox."""

    code = "phantom.sandbox.timeout"

    def __init__(self, command: str, *, deadline_s: float) -> None:
        super().__init__(f"command exceeded {deadline_s:.1f}s wall-clock deadline: {command}")
        self.command = command
        self.deadline_s = deadline_s


class SandboxBlockedError(SandboxError):
    """The command was blocked before it ever entered the sandbox.

    Examples: matched the permanent blocklist; reverse-shell pattern; God
    Mode TTL expired and operator policy is "fail closed".
    """

    code = "phantom.sandbox.blocked"


class SandboxOutputTruncatedError(SandboxError):
    """Stdout or stderr exceeded its cap. Raised when the operator opted in
    to ``raise_on_truncation`` (default ``False`` — usually we just truncate
    silently and annotate the result)."""

    code = "phantom.sandbox.output_truncated"


# ─── Other domains (filled in at later stages) ────────────────────────────────


class PluginError(PhantomError):
    """Plugin load / activate / call failure (Stage 2+)."""

    code = "phantom.plugin"


class ChannelError(PhantomError):
    """Channel adapter failure (Stage 3+)."""

    code = "phantom.channel"


class ProtocolError(PhantomError):
    """Wire-format violation in MCP or ACP (Stage 4+)."""

    code = "phantom.protocol"


class LicenseError(PhantomError):
    """Pro-tier license validation failed."""

    code = "phantom.license"
