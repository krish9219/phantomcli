"""
Persistent context memory — mirrors Claude Code's CLAUDE.md hierarchy.

Phantom auto-loads `CONTEXT.md` files from three scopes and merges them
into the system prompt at session start and whenever `/memory reload` is
called. Layering (lowest precedence first — later layers win on
duplicate keys in the future; for now we concatenate in order):

  1. user         — ~/.phantom/CONTEXT.md             (personal defaults)
  2. project_root — <cwd root>/.phantom/CONTEXT.md    (team-shared, checked in)
  3. local        — nearest parent dir's CONTEXT.md   (work-area scoped)

Size budgeting: the merged block is truncated to `max_chars` (default 16 KB)
from the TAIL of each file so recent guidance wins if a single file is huge.
A banner is injected at the top of the merged block so the model understands
what it's reading.

API:
  discover(start=cwd)    → list[LoadedFile]      (all files found, in order)
  load(start=cwd, max_chars=16_000) → MergedContext
  inject_into_messages(msgs, merged) → new msgs with a system message prepended
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("omnicli.context_memory")

_USER_BASENAME    = os.path.join(".phantom", "CONTEXT.md")
_PROJECT_BASENAME = os.path.join(".phantom", "CONTEXT.md")
_LOCAL_BASENAME   = "CONTEXT.md"   # any directory can drop one

DEFAULT_MAX_CHARS = 16_000
MAX_PER_FILE      = 8_000


@dataclass
class LoadedFile:
    path:    str
    scope:   str   # "user" | "project_root" | "local"
    content: str
    truncated: bool = False


@dataclass
class MergedContext:
    files: list[LoadedFile] = field(default_factory=list)
    text:  str = ""
    total_chars: int = 0

    @property
    def empty(self) -> bool:
        return not self.files or not self.text.strip()


# ─── Discovery ──────────────────────────────────────────────────────────────


def _user_path() -> str:
    return os.environ.get(
        "PHANTOM_CONTEXT_USER",
        os.path.expanduser("~/" + _USER_BASENAME),
    )


def _project_root(start: Optional[str]) -> Optional[str]:
    """Walk up from `start` looking for a `.phantom/` directory."""
    p = Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / ".phantom").is_dir():
            return str(d)
    return None


def _local_file(start: Optional[str]) -> Optional[str]:
    """Walk up from `start` looking for the nearest CONTEXT.md (outside
    ~/.phantom/ and the project .phantom/ dir). This gives workspace-local
    guidance, e.g. `projects/X/subproject/CONTEXT.md`."""
    p = Path(start or os.getcwd()).resolve()
    home = Path(os.path.expanduser("~")).resolve()
    root = _project_root(start)
    project_root_path = Path(root).resolve() if root else None
    for d in (p, *p.parents):
        if d == home:
            break
        candidate = d / _LOCAL_BASENAME
        # Don't double-load the project CONTEXT.md (that's covered by project_root scope)
        if project_root_path and d == project_root_path:
            continue
        if candidate.is_file():
            return str(candidate)
    return None


def discover(start: Optional[str] = None) -> list[LoadedFile]:
    """Return the list of CONTEXT files that would be loaded, in order."""
    out: list[LoadedFile] = []
    # 1) user
    u = _user_path()
    if os.path.isfile(u):
        out.append(LoadedFile(path=u, scope="user", content=_read(u)))
    # 2) project root
    root = _project_root(start)
    if root:
        rp = os.path.join(root, _PROJECT_BASENAME)
        if os.path.isfile(rp):
            out.append(LoadedFile(path=rp, scope="project_root", content=_read(rp)))
    # 3) local
    local = _local_file(start)
    if local:
        out.append(LoadedFile(path=local, scope="local", content=_read(local)))
    return out


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as e:
        log.warning("cannot read %s: %s", path, e)
        return ""


# ─── Loading ────────────────────────────────────────────────────────────────


def load(start: Optional[str] = None, max_chars: int = DEFAULT_MAX_CHARS) -> MergedContext:
    """Discover, enforce per-file budget, concatenate, enforce overall budget."""
    found = discover(start)
    if not found:
        return MergedContext(files=[], text="", total_chars=0)

    # Per-file tail truncation first (keep the latest guidance if a file is huge).
    for f in found:
        if len(f.content) > MAX_PER_FILE:
            tail = f.content[-MAX_PER_FILE:]
            f.content = tail
            f.truncated = True

    # Build the merged block with scope headers the model can see.
    parts: list[str] = [
        "# PHANTOM CONTEXT — auto-loaded at session start",
        "The following CONTEXT.md files are attached. Treat them as "
        "high-priority guidance. If multiple files contradict, prefer the "
        "most-specific scope (local > project_root > user).",
    ]
    for f in found:
        parts.append("")
        parts.append(f"## [{f.scope}] {f.path}")
        parts.append(f.content.rstrip())
        if f.truncated:
            parts.append(f"\n_(truncated to last {MAX_PER_FILE} chars — file was longer)_")

    text = "\n".join(parts)
    # Overall budget: trim from the *front* of the user scope if over.
    if len(text) > max_chars:
        head_keep = max_chars // 3
        tail_keep = max_chars - head_keep - 50
        text = (
            text[:head_keep]
            + f"\n\n…[context truncated: {len(text) - max_chars} chars removed]…\n\n"
            + text[-tail_keep:]
        )
    return MergedContext(files=found, text=text, total_chars=len(text))


# ─── Injection ──────────────────────────────────────────────────────────────


def inject_into_messages(messages: list[dict], merged: MergedContext) -> list[dict]:
    """Return a NEW list with the merged context added as the first system
    message (or appended after any existing system block)."""
    if merged.empty:
        return list(messages)
    new = list(messages)
    # Find the last system message index so we keep them grouped at the top.
    last_sys = -1
    for i, m in enumerate(new):
        if m.get("role") == "system":
            last_sys = i
    ctx_msg = {"role": "system", "content": merged.text}
    if last_sys < 0:
        new.insert(0, ctx_msg)
    else:
        new.insert(last_sys + 1, ctx_msg)
    return new


__all__ = [
    "discover", "load", "inject_into_messages",
    "LoadedFile", "MergedContext",
    "DEFAULT_MAX_CHARS", "MAX_PER_FILE",
]
