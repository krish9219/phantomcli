"""
Pattern-based permission layer.

Replaces the flat trust-integer model (1..4) with a scoped allow/deny list
similar to Claude Code's `settings.json` permissions.

A permission pattern has the form `<action>:<target>` where:
  * action ∈ {bash, read, write, edit, browse, search}
  * target is a glob against the action's primary argument:
      - bash → command prefix/glob, e.g. `git:*`, `ls`, `npm run *`
      - read / write / edit → path glob, e.g. `~/projects/**`, `/tmp/*.log`
      - browse / search → URL or domain glob, e.g. `https://github.com/**`

Patterns:
  - `*` matches one path segment / one token
  - `**` matches any number of path segments / tokens
  - A leading `~` is expanded to the user's home directory for path targets
  - Bash action targets are matched against the tokenized command — the
    first non-env token is the program, and subsequent args are matched
    as additional pattern segments if the pattern contains spaces.

Checks:
  * `Permissions.check(action, target)` → ("allow" | "deny" | "ask", reason)
  * Empty config falls back to legacy trust-integer semantics so existing
    installs keep working.

Config keys (via omnicli.memory):
  permissions_allow  : newline-separated patterns
  permissions_deny   : newline-separated patterns (deny wins)
  permissions_ask    : newline-separated patterns (ask if matched, unless allow wins)

Precedence:
  deny > allow > ask > (legacy trust fallback)
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from typing import Iterable, Literal


Decision = Literal["allow", "deny", "ask"]
Action = Literal["bash", "read", "write", "edit", "browse", "search"]


# ─── Pattern expansion ────────────────────────────────────────────────────────


def _expand_home(p: str) -> str:
    if p.startswith("~"):
        return os.path.expanduser(p)
    return p


def _normalize_path(p: str) -> str:
    """Expand ~, absolutize, collapse .. — so patterns can't be tricked by
    equivalent spellings of the same path."""
    p = _expand_home(p)
    try:
        p = os.path.abspath(p)
    except Exception:
        pass
    return p


# ─── Glob matching ───────────────────────────────────────────────────────────
# fnmatch alone doesn't handle ** across path separators the way users expect.
# We translate `**` → match-any-including-separators and leave `*` as
# match-within-segment.


def _glob_to_regex(pattern: str, sep: str = "/") -> re.Pattern:
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*" and i + 1 < len(pattern) and pattern[i + 1] == "*":
            out.append(".*")
            i += 2
            # swallow an optional trailing / after **
            if i < len(pattern) and pattern[i] == sep:
                i += 1
        elif c == "*":
            out.append(f"[^{re.escape(sep)}]*")
            i += 1
        elif c == "?":
            out.append(f"[^{re.escape(sep)}]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _path_match(pattern: str, target: str) -> bool:
    """Match a path-style pattern (with ** support) against a target path."""
    pat = _expand_home(pattern)
    tgt = _normalize_path(target)
    if not os.path.isabs(pat):
        # Relative pattern — match against the basename AND the tail.
        return fnmatch.fnmatch(tgt, "*/" + pat) or fnmatch.fnmatch(os.path.basename(tgt), pat)
    return _glob_to_regex(pat, sep=os.sep).match(tgt) is not None


def _bash_match(pattern: str, command: str) -> bool:
    """Match a bash-style pattern (e.g. `git:*`, `npm run *`) against a command.

    `git:*` style (colon-separated) matches any command whose program is `git`.
    Space-separated patterns match by tokens.
    """
    cmd = command.strip()
    if not cmd:
        return False
    # Strip leading env assignments: `FOO=bar git status` → program = git
    tokens = cmd.split()
    while tokens and re.match(r"^[A-Z_][A-Z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return False
    program = tokens[0]
    args = " ".join(tokens[1:])

    if ":" in pattern:
        prog_pat, arg_pat = pattern.split(":", 1)
        if not fnmatch.fnmatchcase(program, prog_pat):
            return False
        return fnmatch.fnmatchcase(args, arg_pat) if arg_pat else True
    # Plain pattern — match full command against tokens
    return fnmatch.fnmatchcase(cmd, pattern) or fnmatch.fnmatchcase(program, pattern)


def _url_match(pattern: str, url: str) -> bool:
    return fnmatch.fnmatchcase(url, pattern)


_ACTION_MATCHERS = {
    "bash":   _bash_match,
    "read":   _path_match,
    "write":  _path_match,
    "edit":   _path_match,
    "browse": _url_match,
    "search": _url_match,
}


# ─── Config loading ──────────────────────────────────────────────────────────


def _load_patterns(key: str) -> list[str]:
    """Read a newline-separated list from config. Blank lines and `#`
    comments are ignored."""
    try:
        from omnicli.memory import get_config
    except ImportError:
        return []
    raw = get_config(key, "") or ""
    out = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


# ─── Public API ──────────────────────────────────────────────────────────────


@dataclass
class PermissionResult:
    decision: Decision
    reason: str = ""
    matched_pattern: str = ""


class Permissions:
    """Pattern-based permission checker. Instantiate once per session."""

    def __init__(
        self,
        allow: Iterable[str] | None = None,
        deny:  Iterable[str] | None = None,
        ask:   Iterable[str] | None = None,
    ):
        self.allow = list(allow) if allow is not None else _load_patterns("permissions_allow")
        self.deny  = list(deny)  if deny  is not None else _load_patterns("permissions_deny")
        self.ask   = list(ask)   if ask   is not None else _load_patterns("permissions_ask")

    def check(self, action: str, target: str, *, audit: bool = True) -> PermissionResult:
        """Returns a PermissionResult; precedence: deny > allow > ask.

        Every decision is appended to the audit log (hash-chained, see
        omnicli.audit_log) unless `audit=False`. Auditing failures are
        swallowed so a broken log doesn't break the permission system."""
        if action not in _ACTION_MATCHERS:
            r = PermissionResult("ask", f"unknown action {action!r}")
            _audit_decision("permission", r, action, target, audit)
            return r

        matcher = _ACTION_MATCHERS[action]
        prefix = f"{action}:"

        def _scan(patterns: list[str]) -> str | None:
            for p in patterns:
                if not p.startswith(prefix):
                    continue
                sub = p[len(prefix):]
                if matcher(sub, target):
                    return p
            return None

        hit = _scan(self.deny)
        if hit:
            r = PermissionResult("deny",  f"matched deny pattern {hit!r}", hit)
            _audit_decision("permission", r, action, target, audit)
            return r
        hit = _scan(self.allow)
        if hit:
            r = PermissionResult("allow", f"matched allow pattern {hit!r}", hit)
            _audit_decision("permission", r, action, target, audit)
            return r
        hit = _scan(self.ask)
        if hit:
            r = PermissionResult("ask",   f"matched ask pattern {hit!r}",   hit)
            _audit_decision("permission", r, action, target, audit)
            return r
        r = PermissionResult("ask", "no matching pattern")
        _audit_decision("permission", r, action, target, audit)
        return r

    def has_config(self) -> bool:
        """True iff the user has configured any permission patterns.
        Callers can use this to decide whether to defer to legacy trust."""
        return bool(self.allow or self.deny or self.ask)


def load() -> Permissions:
    """Singleton-ish loader. Re-reads config each time so changes via
    `/perm allow X` are picked up without restarting."""
    return Permissions()


def _audit_decision(category: str, result: "PermissionResult",
                    action: str, target: str, enabled: bool) -> None:
    """Append a permission decision to the audit log. Best-effort."""
    if not enabled:
        return
    try:
        from omnicli.audit_log import record as _audit
        _audit(
            category=category,
            decision=result.decision,   # "allow" | "deny" | "ask"
            subject=action,
            resource=target,
            reason=result.reason,
            matched_pattern=result.matched_pattern,
        )
    except Exception:
        pass


__all__ = ["Permissions", "PermissionResult", "load"]
