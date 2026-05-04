"""Write-ahead log for edit transactions — crash-recovery on next start.

Why
---

The in-memory snapshots in :class:`EditTransaction` protect against
errors **during the commit**. They do not protect against the process
being killed (SIGKILL, power loss, OOM) between the first file write
and the rest. The WAL closes that hole.

How
---

When :meth:`EditTransaction.commit` is called with WAL enabled:

1. Before any file is written, the snapshot of every target is dumped
   to ``<wal_dir>/<txn_id>.wal`` (atomic write).
2. After every file write succeeds, the WAL is renamed to
   ``<txn_id>.committed`` — a marker file with no contents.
3. On clean exit, both files are deleted.

If the process dies between step 1 and step 2, on next start
:func:`recover_pending` finds the .wal file (no .committed sibling) and
restores every snapshot. If the process dies between step 2 and step 3,
recovery is a no-op (the commit was complete).

Format
------

WAL is JSON-on-disk. Each entry: ``{"path": ..., "body_b64": ...,
"mode": <int|None>, "existed": <bool>}``. Encoding is verbose (binary
files would force base64 anyway, and JSON debuggability beats binary
compactness for a recovery log).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "WAL_VERSION",
    "WalEntry",
    "WalRecord",
    "default_wal_dir",
    "list_pending",
    "recover_pending",
    "write_wal",
    "mark_committed",
    "delete_wal",
]

log = logging.getLogger("phantom.edits.wal")

WAL_VERSION: int = 1


def default_wal_dir() -> Path:
    base = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom"))
    p = base / "edit-wal"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


@dataclass(frozen=True, slots=True)
class WalEntry:
    path: str
    body_b64: Optional[str]   # None means "did not exist before"
    mode: Optional[int]
    existed: bool


@dataclass(frozen=True, slots=True)
class WalRecord:
    txn_id: str
    started_at: float
    entries: tuple[WalEntry, ...] = field(default_factory=tuple)
    description: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "version": WAL_VERSION,
            "txn_id": self.txn_id,
            "started_at": self.started_at,
            "description": self.description,
            "entries": [
                {
                    "path": e.path,
                    "body_b64": e.body_b64,
                    "mode": e.mode,
                    "existed": e.existed,
                }
                for e in self.entries
            ],
        })

    @classmethod
    def from_json(cls, raw: str) -> "WalRecord":
        obj = json.loads(raw)
        if obj.get("version") != WAL_VERSION:
            raise ValueError(f"unsupported WAL version: {obj.get('version')}")
        entries = tuple(
            WalEntry(
                path=str(e["path"]),
                body_b64=e.get("body_b64"),
                mode=e.get("mode"),
                existed=bool(e.get("existed", False)),
            )
            for e in obj.get("entries") or []
        )
        return cls(
            txn_id=str(obj["txn_id"]),
            started_at=float(obj["started_at"]),
            description=str(obj.get("description", "")),
            entries=entries,
        )


# ─── public API ─────────────────────────────────────────────────────────────


def write_wal(record: WalRecord, *, wal_dir: Optional[Path] = None) -> Path:
    """Atomically write the WAL for a transaction.

    Returns the .wal file path. Caller passes the same path to
    :func:`mark_committed` after the commit succeeds.
    """
    d = wal_dir or default_wal_dir()
    target = d / f"{record.txn_id}.wal"
    body = record.to_json()
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(d), delete=False,
        prefix=f".{record.txn_id}.", suffix=".tmp",
    )
    try:
        tmp.write(body)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def mark_committed(wal_path: Path) -> None:
    """Mark a transaction as committed by renaming .wal → .committed."""
    if not wal_path.exists():
        return
    committed = wal_path.with_suffix(".committed")
    os.replace(wal_path, committed)


def delete_wal(wal_path: Path) -> None:
    """Remove the WAL artifact (.wal or .committed) on clean exit."""
    if wal_path.exists():
        wal_path.unlink()
    committed = wal_path.with_suffix(".committed")
    if committed.exists():
        committed.unlink()


def list_pending(wal_dir: Optional[Path] = None) -> list[Path]:
    """Return every .wal file with NO matching .committed sibling."""
    d = wal_dir or default_wal_dir()
    if not d.exists():
        return []
    out: list[Path] = []
    for f in sorted(d.glob("*.wal")):
        if not f.with_suffix(".committed").exists():
            out.append(f)
    return out


def recover_pending(wal_dir: Optional[Path] = None) -> list[tuple[Path, str]]:
    """Restore every pending transaction.

    Returns ``[(wal_path, summary), ...]`` describing each recovery.
    Removes the WAL after a successful restore. If a single entry's
    restore fails, the entire WAL is left in place (manual cleanup) so
    nothing is silently lost.
    """
    pending = list_pending(wal_dir)
    summaries: list[tuple[Path, str]] = []
    for wal_path in pending:
        try:
            record = WalRecord.from_json(wal_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as e:
            log.error("WAL %s is corrupt: %s — leaving in place", wal_path, e)
            summaries.append((wal_path, f"CORRUPT: {e}"))
            continue
        ok = True
        for entry in record.entries:
            try:
                _restore_entry(entry)
            except OSError as e:
                log.error("WAL %s: failed to restore %s: %s", wal_path, entry.path, e)
                ok = False
                summaries.append((wal_path, f"PARTIAL: failed at {entry.path}: {e}"))
                break
        if ok:
            wal_path.unlink()
            summaries.append((wal_path, f"recovered {len(record.entries)} files (txn {record.txn_id})"))
    return summaries


def _restore_entry(entry: WalEntry) -> None:
    p = Path(entry.path)
    if not entry.existed:
        # File didn't exist before the transaction; remove whatever's there.
        if p.exists():
            p.unlink()
        return
    if entry.body_b64 is None:
        # Defensive — should not happen if existed=True, but treat as
        # "restore to empty file" rather than crash.
        body = b""
    else:
        body = base64.b64decode(entry.body_b64)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    if entry.mode is not None:
        try:
            os.chmod(p, entry.mode)
        except OSError:
            pass


def make_txn_id() -> str:
    return f"txn-{int(time.time())}-{uuid.uuid4().hex[:8]}"
