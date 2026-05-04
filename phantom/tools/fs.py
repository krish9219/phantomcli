"""Filesystem read/write/list with operator allow-listing.

These tools let the agent operate on files outside the sandboxed
shell context (e.g. read a file the user pasted a path to). Every
call validates the requested path against an explicit allow-list of
prefix paths; the default agent loop passes the session's workdir.

Why a separate tool from the sandbox? The sandbox is for *running
code*. Reading a config file you point Phantom at doesn't need a
fresh subprocess. These tools never execute anything — they only
read or write data.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from phantom.errors import PermissionDeniedError, PhantomError

__all__ = ["edit_file", "list_dir", "read_file", "write_file"]


def _resolve_within(path: str, allowlist: tuple[str, ...]) -> Path:
    """Validate that *path* is within at least one allowlist prefix.

    Resolves symlinks before the check so a sneaky symlink that
    points outside the allowlist is rejected.
    """
    if not path:
        raise PhantomError("path is required")
    if not allowlist:
        raise PhantomError("allowlist is empty — refusing all paths")
    candidate = Path(path).expanduser().resolve()
    norm_allow = tuple(Path(a).expanduser().resolve() for a in allowlist)
    if not any(_is_within(candidate, a) for a in norm_allow):
        raise PermissionDeniedError(
            f"path {path!r} is not in allowlist {[str(a) for a in norm_allow]}"
        )
    return candidate


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def read_file(
    *,
    path: str,
    allowlist: tuple[str, ...],
    max_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    """Read *path* as UTF-8.

    Returns a dict: ``ok, path, text, size_bytes, truncated, error``.
    Never raises on file-not-found; returns ``ok=False`` instead.
    """
    if max_bytes < 1024:
        raise PhantomError("max_bytes must be ≥ 1024")
    try:
        target = _resolve_within(path, allowlist)
    except (PhantomError, PermissionDeniedError) as exc:
        return {"ok": False, "path": path, "text": "",
                "size_bytes": 0, "truncated": False,
                "error": exc.detail or str(exc)}

    if not target.exists():
        return {"ok": False, "path": str(target), "text": "",
                "size_bytes": 0, "truncated": False, "error": "not found"}
    if not target.is_file():
        return {"ok": False, "path": str(target), "text": "",
                "size_bytes": 0, "truncated": False,
                "error": "not a regular file"}

    try:
        size = target.stat().st_size
        truncated = size > max_bytes
        with target.open("rb") as fh:
            data = fh.read(max_bytes)
        text = data.decode("utf-8", errors="replace")
        if truncated:
            text += "\n[phantom: file truncated]"
        return {
            "ok": True, "path": str(target), "text": text,
            "size_bytes": size, "truncated": truncated, "error": "",
        }
    except OSError as exc:
        return {"ok": False, "path": str(target), "text": "",
                "size_bytes": 0, "truncated": False,
                "error": f"{type(exc).__name__}: {exc}"}


def write_file(
    *,
    path: str,
    text: str,
    allowlist: tuple[str, ...],
) -> dict[str, Any]:
    """Write *text* (UTF-8) to *path*.

    Creates parent directories as needed. Refuses paths outside the
    allow-list. Returns a dict: ``ok, path, bytes_written, error``.
    """
    try:
        target = _resolve_within(path, allowlist)
    except (PhantomError, PermissionDeniedError) as exc:
        return {"ok": False, "path": path, "bytes_written": 0,
                "error": exc.detail or str(exc)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = text.encode("utf-8")
        target.write_bytes(encoded)
        return {
            "ok": True, "path": str(target),
            "bytes_written": len(encoded), "error": "",
        }
    except OSError as exc:
        return {"ok": False, "path": str(target), "bytes_written": 0,
                "error": f"{type(exc).__name__}: {exc}"}


def edit_file(
    *,
    path: str,
    old_string: str,
    new_string: str,
    allowlist: tuple[str, ...],
    replace_all: bool = False,
) -> dict[str, Any]:
    """Replace ``old_string`` with ``new_string`` inside *path*.

    Exact-match replacement, no regex. By default fails when
    ``old_string`` is not unique in the file — matches Claude Code's
    Edit semantics. Set ``replace_all=True`` to substitute every
    occurrence.

    Returns ``ok, path, replacements, error``. Never raises on a missing
    file or no-match; returns ``ok=False`` instead.
    """
    if not isinstance(old_string, str) or not old_string:
        return {"ok": False, "path": path, "replacements": 0,
                "error": "old_string must be a non-empty string"}
    if not isinstance(new_string, str):
        return {"ok": False, "path": path, "replacements": 0,
                "error": "new_string must be a string"}
    if old_string == new_string:
        return {"ok": False, "path": path, "replacements": 0,
                "error": "old_string and new_string are identical"}
    try:
        target = _resolve_within(path, allowlist)
    except (PhantomError, PermissionDeniedError) as exc:
        return {"ok": False, "path": path, "replacements": 0,
                "error": exc.detail or str(exc)}

    if not target.exists():
        return {"ok": False, "path": str(target), "replacements": 0,
                "error": "not found"}
    if not target.is_file():
        return {"ok": False, "path": str(target), "replacements": 0,
                "error": "not a regular file"}
    try:
        original = target.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "path": str(target), "replacements": 0,
                "error": f"{type(exc).__name__}: {exc}"}
    except UnicodeDecodeError as exc:
        return {"ok": False, "path": str(target), "replacements": 0,
                "error": f"UnicodeDecodeError: {exc}"}

    occurrences = original.count(old_string)
    if occurrences == 0:
        return {"ok": False, "path": str(target), "replacements": 0,
                "error": "old_string not found in file"}
    if occurrences > 1 and not replace_all:
        return {"ok": False, "path": str(target), "replacements": 0,
                "error": (f"old_string is not unique ({occurrences} "
                          "matches); pass replace_all=True or extend "
                          "old_string with surrounding context")}

    if replace_all:
        updated = original.replace(old_string, new_string)
        replaced = occurrences
    else:
        updated = original.replace(old_string, new_string, 1)
        replaced = 1
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "path": str(target), "replacements": 0,
                "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "path": str(target), "replacements": replaced, "error": ""}


def list_dir(
    *,
    path: str,
    allowlist: tuple[str, ...],
) -> dict[str, Any]:
    """List entries directly under *path*.

    Returns a dict: ``ok, path, entries, error`` where ``entries`` is
    a list of ``{name, kind, size}`` dicts. ``kind`` is one of
    ``"file"`` / ``"dir"`` / ``"link"`` / ``"other"``.
    """
    try:
        target = _resolve_within(path, allowlist)
    except (PhantomError, PermissionDeniedError) as exc:
        return {"ok": False, "path": path, "entries": [],
                "error": exc.detail or str(exc)}
    if not target.exists():
        return {"ok": False, "path": str(target), "entries": [],
                "error": "not found"}
    if not target.is_dir():
        return {"ok": False, "path": str(target), "entries": [],
                "error": "not a directory"}
    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(target.iterdir()):
            stat = child.lstat()
            kind = (
                "link" if child.is_symlink()
                else "dir" if child.is_dir()
                else "file" if child.is_file()
                else "other"
            )
            entries.append({
                "name": child.name,
                "kind": kind,
                "size": stat.st_size if kind == "file" else 0,
            })
    except OSError as exc:
        return {"ok": False, "path": str(target), "entries": entries,
                "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "path": str(target), "entries": entries, "error": ""}
