"""Phantom edits — transactional multi-file changes with rollback.

The agent's edit tool today writes one file at a time, no rollback. If
write 3 of 5 succeeds and write 4 fails, you're left in a half-edited
state. This module gives the agent a real transaction:

* Stage edits in memory.
* Generate a unified-diff preview.
* Commit atomically — write all files, snapshot first, restore all on
  any failure.
* Roll back on demand.

Public surface
--------------

* :class:`EditOp` — one file's old/new content.
* :class:`EditTransaction` — collects edits, previews diffs, commits or
  rolls back.
* :func:`unified_diff_for` — pure-function preview helper.
"""

from __future__ import annotations

from phantom.edits.transaction import (
    EditOp,
    EditTransaction,
    EditTransactionError,
    TransactionResult,
    unified_diff_for,
)
from phantom.edits.wal import (
    WalEntry,
    WalRecord,
    list_pending,
    recover_pending,
)

__all__ = [
    "EditOp",
    "EditTransaction",
    "EditTransactionError",
    "TransactionResult",
    "WalEntry",
    "WalRecord",
    "list_pending",
    "recover_pending",
    "unified_diff_for",
]
