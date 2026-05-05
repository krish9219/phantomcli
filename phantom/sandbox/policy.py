"""Sandbox policy — declarative spec for what a sandboxed call may do.

A :class:`SandboxPolicy` is a frozen, hashable dataclass that describes
the security envelope for a single :func:`phantom.sandbox.run` invocation.
Every field has a documented default; callers override only what they
need.

Design constraints
------------------

* **Immutable.** Once a policy is built, it cannot be mutated. This makes
  policies safe to reuse across calls and trivial to audit.
* **Backend-agnostic.** The policy describes *intent* (no network, this
  filesystem layout, these resource ceilings). Translating intent into a
  particular backend's CLI flags is the backend's job, not the policy's.
* **Validated at construction time.** A bad policy fails fast in the
  caller's stack frame, not deep inside a backend launch where the
  traceback is useless.
* **Explicit over implicit.** ``deny_paths`` is a fixed list of
  high-value targets we *always* hide, even when the operator has not
  asked us to. ``read_only_paths`` and ``writable_paths`` are explicit;
  there is no "everything is writable by default" mode.

Examples
--------

>>> from phantom.sandbox.policy import SandboxPolicy, ResourceLimits
>>> p = SandboxPolicy(
...     workdir="/tmp/job",
...     writable_paths=("/tmp/job",),
...     network=False,
...     limits=ResourceLimits(wall_s=30.0, rss_mib=512, cpu_s=20.0),
... )
>>> p.network
False
>>> p.limits.wall_s
30.0
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

from phantom.errors import ConfigError

__all__ = [
    "DEFAULT_DENY_PATHS",
    "ResourceLimits",
    "SandboxPolicy",
]


# ─── Default deny-list ────────────────────────────────────────────────────────
# Paths we hide from every sandboxed process unless the operator explicitly
# adds them to ``writable_paths`` or ``read_only_paths``. The list is
# conservative; operators who need to expose one of these must do so
# consciously. The names are the canonical Linux paths; backends translate
# to platform-specific equivalents (e.g. macOS ``/Users/<u>/.aws``).

DEFAULT_DENY_PATHS: Final[tuple[str, ...]] = (
    # SSH keys
    "~/.ssh",
    # Cloud credentials
    "~/.aws",
    "~/.azure",
    "~/.config/gcloud",
    "~/.kube",
    # Browser profiles (cookies, saved passwords)
    "~/.config/google-chrome",
    "~/.config/chromium",
    "~/.mozilla",
    # Password managers
    "~/.password-store",
    "~/.gnupg",
    # Shell history (often leaks secrets)
    "~/.bash_history",
    "~/.zsh_history",
    "~/.local/share/fish/fish_history",
    # Phantom's own config (the agent should not be able to rewrite its
    # own license, audit log, or memory DB from inside a sandboxed tool).
    "~/.phantom",
    "~/.omnicli",
    # System secrets
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/sudoers.d",
    "/root/.ssh",
)


# ─── Resource limits ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Process-scoped resource ceilings.

    Fields are abstract; backends translate to ``ulimit``, ``prlimit``,
    docker ``--cpus``/``--memory``, etc. A value of ``None`` for a field
    means "do not enforce" (rely on the backend default), but every
    field has a sensible non-``None`` default that makes refusing-by-
    default the easy path.

    Validation rules:

    * ``wall_s`` and ``cpu_s`` must be > 0 if set.
    * ``cpu_s <= wall_s`` (CPU budget cannot exceed the wall-clock budget).
    * ``rss_mib`` must be > 0 if set, and ≤ 16 GiB (16384 MiB).
    * ``fds`` must be > 0 if set, and ≤ 65535 (system FD ceiling).
    * ``stdout_bytes`` and ``stderr_bytes`` must be ≥ 4 KiB if set.
    """

    wall_s: float = 300.0
    """Wall-clock deadline. Hard kill at this point."""

    cpu_s: float | None = 60.0
    """CPU-time deadline. Soft signal first (SIGXCPU), then SIGKILL."""

    rss_mib: int | None = 512
    """Resident-set ceiling in MiB."""

    fds: int | None = 256
    """File-descriptor ceiling."""

    stdout_bytes: int = 1024 * 1024
    """Maximum bytes captured from stdout. Excess is truncated and the
    result's ``truncated`` flag is set."""

    stderr_bytes: int = 1024 * 1024
    """Maximum bytes captured from stderr."""

    nofork: bool = False
    """If True, the sandboxed process may not fork. Useful for one-shot
    commands; relaxed by default because real shell pipelines fork."""

    def __post_init__(self) -> None:
        if self.wall_s <= 0:
            raise ConfigError(f"wall_s must be > 0, got {self.wall_s}")
        if self.cpu_s is not None:
            if self.cpu_s <= 0:
                raise ConfigError(f"cpu_s must be > 0, got {self.cpu_s}")
            if self.cpu_s > self.wall_s:
                raise ConfigError(
                    f"cpu_s ({self.cpu_s}) must be ≤ wall_s ({self.wall_s})"
                )
        if self.rss_mib is not None:
            if self.rss_mib <= 0:
                raise ConfigError(f"rss_mib must be > 0, got {self.rss_mib}")
            if self.rss_mib > 16384:
                raise ConfigError(
                    f"rss_mib must be ≤ 16384 (16 GiB), got {self.rss_mib}"
                )
        if self.fds is not None:
            if self.fds <= 0:
                raise ConfigError(f"fds must be > 0, got {self.fds}")
            if self.fds > 65535:
                raise ConfigError(f"fds must be ≤ 65535, got {self.fds}")
        if self.stdout_bytes < 4096:
            raise ConfigError(
                f"stdout_bytes must be ≥ 4096 (4 KiB), got {self.stdout_bytes}"
            )
        if self.stderr_bytes < 4096:
            raise ConfigError(
                f"stderr_bytes must be ≥ 4096 (4 KiB), got {self.stderr_bytes}"
            )


# ─── Sandbox policy ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Declarative spec for what a sandboxed call may do.

    Parameters
    ----------
    workdir:
        Process working directory inside the sandbox. Must be one of the
        ``writable_paths`` (or a subdirectory of one).
    writable_paths:
        Tuple of host paths bind-mounted read-write into the sandbox. The
        sandbox sees them at the same paths it had on the host (no
        path translation by default). Empty by default — a paranoid default
        that forces callers to declare what they need.
    read_only_paths:
        Tuple of host paths bind-mounted read-only. Defaults to ``("/usr",
        "/bin", "/lib", "/lib64", "/etc")`` so a typical command can find
        its tools without the operator listing them every time. Operators
        who want a *fully* hermetic sandbox pass an empty tuple.
    deny_paths:
        Tuple of host paths to additionally hide. ``DEFAULT_DENY_PATHS`` is
        always applied first; ``deny_paths`` extends it. ``~`` is expanded
        per-path against the user's home at policy-application time.
    network:
        If False (the default), the sandbox has no network. If True, the
        sandbox shares the host's network namespace. There is no middle
        ground at the policy layer; finer-grained network policy lives in
        backend-specific extensions (e.g. docker network names).
    env:
        Environment-variable allow-list with optional explicit values.
        Keys with ``None`` values are inherited from the parent process if
        present (otherwise omitted). Keys with explicit string values
        override any parent value.
    limits:
        Resource ceilings. Defaults to the cautious :class:`ResourceLimits`
        defaults.
    raise_on_truncation:
        If True, output truncation raises
        :class:`phantom.errors.SandboxOutputTruncatedError` instead of
        returning a result with ``truncated=True``.
    capture_audit:
        If True (the default), every call writes one record to the audit
        log at ``~/.phantom/sandbox-audit.log``.
    """

    workdir: str
    writable_paths: tuple[str, ...] = ()
    read_only_paths: tuple[str, ...] = (
        "/usr",
        "/bin",
        "/lib",
        "/lib64",
        "/etc",
    )
    deny_paths: tuple[str, ...] = ()
    network: bool = False
    env: dict[str, str | None] = field(default_factory=dict)
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    raise_on_truncation: bool = False
    capture_audit: bool = True

    def __post_init__(self) -> None:
        if not self.workdir:
            raise ConfigError("workdir is required")
        # Cross-platform absoluteness: POSIX leading "/" or Windows drive-letter root.
        if not os.path.isabs(self.workdir):
            raise ConfigError(f"workdir must be absolute, got {self.workdir!r}")

        # workdir must be within at least one writable mount.
        wp = tuple(p.rstrip("/") or "/" for p in self.writable_paths)
        wd = self.workdir.rstrip("/") or "/"
        # "/" as a writable mount accepts every absolute workdir — we
        # special-case it because the startswith trick below would
        # check "//", which fails for `/tmp/job`.
        ok = any(p == "/" or wd == p or wd.startswith(p + "/") for p in wp)
        if not ok:
            raise ConfigError(
                f"workdir {self.workdir!r} must be inside one of writable_paths "
                f"{self.writable_paths}"
            )

        # Read-only and writable must not overlap. We allow nesting only if
        # the writable path is *deeper* than the read-only one (the writable
        # bind-mount masks the read-only one for that subtree). Equality is
        # forbidden: ambiguous.
        for r in self.read_only_paths:
            r_norm = r.rstrip("/") or "/"
            for w in wp:
                if r_norm == w:
                    raise ConfigError(
                        f"path {r!r} is in both writable_paths and read_only_paths"
                    )

    def expanded_deny_paths(self, *, home: str) -> tuple[str, ...]:
        """Return ``DEFAULT_DENY_PATHS + self.deny_paths`` with ``~`` expanded.

        ``home`` is passed in (not read from os.environ) so this method is
        deterministic and unit-testable. Backends that need the actual user
        home read it from the policy's environment or from the sandboxed
        process's HOME (whichever the backend chooses).
        """
        # Normalise home: trim trailing slashes, but keep "/" as "" for the
        # special root case so "~/.ssh" → "/.ssh" instead of "//.ssh".
        home_norm = home.rstrip("/")
        all_paths = DEFAULT_DENY_PATHS + tuple(self.deny_paths)
        expanded: list[str] = []
        for p in all_paths:
            if p.startswith("~"):
                expanded.append(home_norm + p[1:])
            else:
                expanded.append(p)
        # Stable order, deduplicated.
        seen: set[str] = set()
        out: list[str] = []
        for p in expanded:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return tuple(out)
