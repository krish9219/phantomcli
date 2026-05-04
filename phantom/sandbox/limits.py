"""Resource-limit translation — abstract :class:`ResourceLimits` to backend args.

Each backend has a different syntax for "cap RSS at 512 MiB" or "kill after
60s of CPU time". This module owns the translation. Backends call into it;
they do not encode the syntax themselves. That separation keeps the limit
logic testable (one set of unit tests covers the whole matrix) and makes
adding a new backend a five-line change.

The functions here return *additional argv tokens* to prepend to the
command, plus a list of environment variables to inject. Backends compose
these with their own setup arguments.

Example
-------

>>> from phantom.sandbox.policy import ResourceLimits
>>> from phantom.sandbox.limits import prlimit_args
>>> prlimit_args(ResourceLimits(wall_s=30.0, cpu_s=20.0, rss_mib=512, fds=256))
['prlimit', '--cpu=20', '--as=536870912', '--nofile=256']
"""

from __future__ import annotations

from phantom.sandbox.policy import ResourceLimits

__all__ = [
    "docker_flags",
    "prlimit_args",
    "ulimit_shell_prefix",
]


_MIB = 1024 * 1024


def prlimit_args(limits: ResourceLimits) -> list[str]:
    """Return ``["prlimit", ...]`` argv prefix for the ``unshare`` backend.

    ``prlimit`` is invoked as a wrapper around the target command:
    ``prlimit --cpu=N --as=BYTES --nofile=FDS -- /bin/sh -c '...'``.

    ``wall_s`` is **not** translated here — wall-clock deadlines are
    enforced by the backend's own ``subprocess.run(timeout=...)`` call,
    not by ``prlimit`` (prlimit's RLIMIT_CPU is CPU-time, not wall-time).
    """
    out: list[str] = ["prlimit"]
    if limits.cpu_s is not None:
        out.append(f"--cpu={int(limits.cpu_s)}")
    if limits.rss_mib is not None:
        # prlimit takes RLIMIT_AS in bytes (address space, which on Linux
        # is the closest available to "RSS" — true RSS is not a settable
        # rlimit). The cgroup-v2 path would be more accurate but requires
        # privileges we don't always have. AS is a strict superset of
        # RSS, so this is conservative — the kill happens slightly later
        # than the operator asked, never sooner.
        out.append(f"--as={limits.rss_mib * _MIB}")
    if limits.fds is not None:
        out.append(f"--nofile={limits.fds}")
    return out


def ulimit_shell_prefix(limits: ResourceLimits) -> str:
    """Return a ``ulimit -…`` shell prefix for backends that wrap a shell.

    Used by backends that already start a ``/bin/sh -c`` subshell (notably
    bwrap and firejail when invoking pipelines). Returns the empty string
    if no limits would be applied.

    Note: the ulimit prefix is *defence in depth* on top of the backend's
    own argv-level limits. If the backend already enforces a ceiling, this
    is harmless duplication.
    """
    parts: list[str] = []
    if limits.cpu_s is not None:
        parts.append(f"ulimit -t {int(limits.cpu_s)}")
    if limits.rss_mib is not None:
        # ulimit -v is in KiB.
        parts.append(f"ulimit -v {limits.rss_mib * 1024}")
    if limits.fds is not None:
        parts.append(f"ulimit -n {limits.fds}")
    if not parts:
        return ""
    return "; ".join(parts) + "; "


def docker_flags(limits: ResourceLimits) -> list[str]:
    """Return docker-run flags for *limits*.

    Docker is the odd one out: it accepts limits as flags on the ``docker
    run`` command line and enforces them via cgroups. We map:

    * ``cpu_s``   → ``--ulimit cpu=N:N``
    * ``rss_mib`` → ``--memory={N}m``
    * ``fds``     → ``--ulimit nofile=N:N``

    ``wall_s`` is enforced at the Python layer, same as for ``unshare``.
    """
    out: list[str] = []
    if limits.cpu_s is not None:
        n = int(limits.cpu_s)
        out += ["--ulimit", f"cpu={n}:{n}"]
    if limits.rss_mib is not None:
        out += ["--memory", f"{limits.rss_mib}m"]
    if limits.fds is not None:
        n = limits.fds
        out += ["--ulimit", f"nofile={n}:{n}"]
    return out
