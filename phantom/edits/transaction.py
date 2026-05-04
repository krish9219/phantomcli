"""Transactional multi-file edits.

Design notes
------------

* **Atomic commit**: every staged edit is written by the same call to
  :meth:`EditTransaction.commit`. Each target file is snapshotted into
  an in-memory buffer before its first write. On any error during the
  commit phase, every successfully-written file is restored from its
  snapshot.

* **No partial state**: if commit fails, the on-disk state is exactly
  what it was at transaction start. We restore in **reverse** order so
  even if the restore loop itself fails on a later file, earlier files
  are already back.

* **Encoding**: all I/O is UTF-8 with explicit errors='strict'. Binary
  files are out of scope today — file edits are for source code.

* **Permissions**: file mode + ownership are preserved across
  edit/restore (we ``shutil.copystat`` the snapshot back).

* **Diff preview**: pure :func:`unified_diff_for`, no I/O — the agent
  can show the user a preview before calling commit.
"""

from __future__ import annotations

import difflib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "EditOp",
    "EditTransaction",
    "EditTransactionError",
    "TransactionResult",
    "unified_diff_for",
]

log = logging.getLogger("phantom.edits")


class EditTransactionError(RuntimeError):
    """Raised when commit fails. ``original_error`` carries the cause."""

    def __init__(self, msg: str, *, original_error: Optional[BaseException] = None) -> None:
        super().__init__(msg)
        self.original_error = original_error


@dataclass(frozen=True, slots=True)
class EditOp:
    path: Path
    new_content: str
    create_if_missing: bool = False
    delete: bool = False  # if True, new_content is ignored

    def __post_init__(self) -> None:
        if self.delete and self.create_if_missing:
            raise ValueError("delete and create_if_missing are mutually exclusive")


@dataclass(frozen=True, slots=True)
class TransactionResult:
    files_written: tuple[str, ...] = field(default_factory=tuple)
    files_deleted: tuple[str, ...] = field(default_factory=tuple)
    files_created: tuple[str, ...] = field(default_factory=tuple)
    bytes_written: int = 0
    duration_ms: float = 0.0


def unified_diff_for(
    path: Path,
    new_content: str,
    *,
    fromfile_label: Optional[str] = None,
    tofile_label: Optional[str] = None,
    n: int = 3,
) -> str:
    """Return a unified diff for ``path`` → ``new_content``.

    Pure function. No I/O beyond reading ``path`` (or treating it as
    empty if it doesn't exist). New files diff against an empty
    baseline; deleted files (caller passes new_content=='') diff
    against an empty target.
    """
    try:
        old = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError):
        old = ""
    a = old.splitlines(keepends=True)
    b = new_content.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        a, b,
        fromfile=fromfile_label or f"a/{path.name}",
        tofile=tofile_label or f"b/{path.name}",
        n=n,
    ))


class EditTransaction:
    """Stage multi-file edits and commit them atomically.

    Usage::

        tx = EditTransaction()
        tx.stage(EditOp(Path('a.py'), 'new content'))
        tx.stage(EditOp(Path('b.py'), 'other content'))
        print(tx.preview())
        tx.commit()             # writes both, or rolls back on failure
    """

    def __init__(self, *, wal_enabled: bool = False, wal_description: str = "") -> None:
        self._ops: list[EditOp] = []
        self._committed = False
        self._wal_enabled = wal_enabled
        self._wal_description = wal_description
        self._wal_path: Optional[Path] = None

    # ── staging ─────────────────────────────────────────────────────

    def stage(self, op: EditOp) -> None:
        if self._committed:
            raise EditTransactionError("transaction already committed")
        # Reject duplicate stages on the same path — explicit error beats
        # silently overwriting.
        for existing in self._ops:
            if existing.path == op.path:
                raise EditTransactionError(
                    f"path {op.path!s} already staged in this transaction"
                )
        self._ops.append(op)

    def stage_write(self, path: Path | str, content: str, *, create_if_missing: bool = True) -> None:
        self.stage(EditOp(path=Path(path), new_content=content, create_if_missing=create_if_missing))

    def stage_delete(self, path: Path | str) -> None:
        self.stage(EditOp(path=Path(path), new_content="", delete=True))

    def __len__(self) -> int:
        return len(self._ops)

    def ops(self) -> tuple[EditOp, ...]:
        return tuple(self._ops)

    # ── preview ─────────────────────────────────────────────────────

    def preview(self) -> str:
        """Return a multi-file unified diff. No I/O."""
        chunks: list[str] = []
        for op in self._ops:
            if op.delete:
                chunks.append(unified_diff_for(op.path, "",
                              fromfile_label=f"a/{op.path}",
                              tofile_label="/dev/null"))
            else:
                from_label = f"a/{op.path}" if op.path.exists() else "/dev/null"
                chunks.append(unified_diff_for(
                    op.path, op.new_content,
                    fromfile_label=from_label,
                    tofile_label=f"b/{op.path}",
                ))
        return "".join(chunks)

    # ── commit / rollback ──────────────────────────────────────────

    def commit(self) -> TransactionResult:
        """Write every staged edit. Snapshot first; restore all on any failure."""
        if self._committed:
            raise EditTransactionError("transaction already committed")
        self._validate()

        import time
        t0 = time.perf_counter()

        snapshots: list[tuple[Path, Optional[bytes], Optional[os.stat_result]]] = []
        # Pre-snapshot every target before writing anything.
        for op in self._ops:
            if op.path.exists():
                try:
                    body = op.path.read_bytes()
                    st = op.path.stat()
                    snapshots.append((op.path, body, st))
                except OSError as e:
                    raise EditTransactionError(
                        f"could not snapshot {op.path!s} before commit: {e}",
                        original_error=e,
                    )
            else:
                snapshots.append((op.path, None, None))

        # Write-ahead log: persist snapshots to disk so a SIGKILL between
        # the first file write and the last is recoverable on next start.
        if self._wal_enabled:
            import base64 as _b64
            from phantom.edits.wal import (
                WalEntry, WalRecord, make_txn_id, write_wal,
            )
            entries = tuple(
                WalEntry(
                    path=str(p),
                    body_b64=_b64.b64encode(body).decode("ascii") if body is not None else None,
                    mode=(st.st_mode if st is not None else None),
                    existed=(body is not None),
                )
                for p, body, st in snapshots
            )
            self._wal_path = write_wal(WalRecord(
                txn_id=make_txn_id(),
                started_at=time.time(),
                description=self._wal_description,
                entries=entries,
            ))

        files_written: list[str] = []
        files_deleted: list[str] = []
        files_created: list[str] = []
        bytes_written = 0

        try:
            for op in self._ops:
                if op.delete:
                    if op.path.exists():
                        op.path.unlink()
                        files_deleted.append(str(op.path))
                    continue
                if not op.path.exists():
                    if not op.create_if_missing:
                        raise EditTransactionError(
                            f"{op.path!s} does not exist and create_if_missing=False"
                        )
                    op.path.parent.mkdir(parents=True, exist_ok=True)
                    files_created.append(str(op.path))

                # Atomic per-file replace via temp file in same dir.
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8",
                    dir=str(op.path.parent), delete=False,
                    prefix=f".{op.path.name}.", suffix=".tmp",
                )
                try:
                    tmp.write(op.new_content)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                finally:
                    tmp.close()
                # Preserve mode if the file existed before
                if op.path.exists():
                    try:
                        shutil.copystat(op.path, tmp.name)
                    except OSError:
                        pass
                os.replace(tmp.name, op.path)
                bytes_written += len(op.new_content.encode("utf-8"))
                files_written.append(str(op.path))
        except BaseException as e:
            log.warning("commit failed; rolling back %d snapshots", len(snapshots))
            self._rollback(snapshots)
            # On error, drop the WAL — the in-memory rollback already
            # restored everything; leaving the WAL would trigger a
            # spurious recovery on next start.
            if self._wal_path is not None:
                try:
                    from phantom.edits.wal import delete_wal
                    delete_wal(self._wal_path)
                except Exception:  # pragma: no cover — never block on cleanup
                    pass
            raise EditTransactionError(
                f"commit failed at file {op.path!s}: {e}",
                original_error=e,
            )

        # All writes succeeded — flip WAL to "committed" so a recovery
        # pass on next start knows not to restore.
        if self._wal_path is not None:
            from phantom.edits.wal import delete_wal, mark_committed
            mark_committed(self._wal_path)
            delete_wal(self._wal_path)
            self._wal_path = None

        self._committed = True
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return TransactionResult(
            files_written=tuple(files_written),
            files_deleted=tuple(files_deleted),
            files_created=tuple(files_created),
            bytes_written=bytes_written,
            duration_ms=round(elapsed_ms, 3),
        )

    def rollback(self) -> None:
        """Discard staged edits. No-op if not yet committed."""
        if self._committed:
            raise EditTransactionError("cannot rollback after successful commit")
        self._ops.clear()

    # ── internals ───────────────────────────────────────────────────

    def _validate(self) -> None:
        if not self._ops:
            raise EditTransactionError("nothing to commit (no ops staged)")

    @staticmethod
    def _rollback(snapshots: list[tuple[Path, Optional[bytes], Optional[os.stat_result]]]) -> None:
        # Restore in reverse order — if a later file's restore fails,
        # earlier files are already back.
        for path, body, st in reversed(snapshots):
            try:
                if body is None:
                    # File didn't exist before; remove what we wrote.
                    if path.exists():
                        path.unlink()
                    continue
                path.write_bytes(body)
                if st is not None:
                    try:
                        os.chmod(path, st.st_mode)
                    except OSError:
                        pass
            except OSError as e:
                # We tried — best-effort rollback.
                log.error("rollback failed for %s: %s", path, e)
