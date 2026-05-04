"""Sandbox result — what every backend returns from a successful launch.

The :class:`SandboxResult` is intentionally narrow: it captures exactly
the information a caller needs to make the next decision (did it succeed?
what did it print? was anything truncated?). It deliberately does *not*
carry a reference back to the policy that produced it — callers that need
the policy keep it in their own scope.

Examples
--------

>>> from phantom.sandbox.result import SandboxResult
>>> r = SandboxResult(
...     stdout="hello\\n",
...     stderr="",
...     exit_code=0,
...     wall_s=0.014,
...     tier="unshare",
...     truncated=False,
... )
>>> r.ok
True
>>> r.exit_code
0
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SandboxResult"]


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """The outcome of a single :func:`phantom.sandbox.run` call.

    Parameters
    ----------
    stdout:
        Decoded stdout (UTF-8, errors='replace'). Possibly truncated to the
        policy's ``stdout_bytes`` cap; in that case the last line is a
        synthetic marker and ``truncated`` is True.
    stderr:
        Same shape as ``stdout`` but for stderr.
    exit_code:
        The process's exit code. By Linux convention, 0 = success, >0 =
        application-defined failure, 128+N = killed by signal N. The
        sandbox reserves the special code ``-1`` for "the sandbox itself
        could not start the process"; that case raises
        :class:`phantom.errors.SandboxLaunchError` and never returns a
        result with ``exit_code = -1``.
    wall_s:
        Wall-clock duration in seconds. Includes time the process spent
        waiting on IO, scheduled out, etc.
    tier:
        Name of the backend that ran the command (``"bwrap"``,
        ``"firejail"``, ``"unshare"``, ``"docker"``).
    truncated:
        True iff stdout or stderr hit its cap.
    """

    stdout: str
    stderr: str
    exit_code: int
    wall_s: float
    tier: str
    truncated: bool

    @property
    def ok(self) -> bool:
        """True iff exit_code == 0. A convenience for shell-style callers."""
        return self.exit_code == 0
