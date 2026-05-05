"""Tests for transactional multi-file edits."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from phantom.edits import (
    EditOp,
    EditTransaction,
    EditTransactionError,
    unified_diff_for,
)


# ─── EditOp validation ───────────────────────────────────────────────────────


def test_editop_rejects_delete_with_create():
    with pytest.raises(ValueError):
        EditOp(path=Path("/tmp/x"), new_content="", create_if_missing=True, delete=True)


# ─── unified_diff_for (pure) ─────────────────────────────────────────────────


def test_unified_diff_new_file(tmp_path: Path):
    p = tmp_path / "new.py"
    diff = unified_diff_for(p, "x = 1\n")
    assert "+x = 1" in diff
    assert "/dev/null" not in diff  # default labels use a/b prefix


def test_unified_diff_changed_file(tmp_path: Path):
    p = tmp_path / "a.py"
    p.write_text("old\n")
    diff = unified_diff_for(p, "new\n")
    assert "-old" in diff
    assert "+new" in diff


def test_unified_diff_no_change_returns_empty(tmp_path: Path):
    p = tmp_path / "same.py"
    p.write_text("same\n")
    assert unified_diff_for(p, "same\n") == ""


# ─── staging ─────────────────────────────────────────────────────────────────


def test_stage_appends_op(tmp_path: Path):
    tx = EditTransaction()
    tx.stage_write(tmp_path / "a", "x")
    tx.stage_write(tmp_path / "b", "y")
    assert len(tx) == 2


def test_stage_rejects_duplicate_path(tmp_path: Path):
    tx = EditTransaction()
    tx.stage_write(tmp_path / "a", "x")
    with pytest.raises(EditTransactionError, match="already staged"):
        tx.stage_write(tmp_path / "a", "y")


def test_stage_after_commit_raises(tmp_path: Path):
    tx = EditTransaction()
    tx.stage_write(tmp_path / "a", "x")
    tx.commit()
    with pytest.raises(EditTransactionError, match="already committed"):
        tx.stage_write(tmp_path / "b", "y")


# ─── preview ─────────────────────────────────────────────────────────────────


def test_preview_includes_every_op(tmp_path: Path):
    a = tmp_path / "a.txt"
    a.write_text("old-a\n")
    b = tmp_path / "b.txt"
    tx = EditTransaction()
    tx.stage_write(a, "new-a\n")
    tx.stage_write(b, "fresh-b\n")
    preview = tx.preview()
    assert "old-a" in preview
    assert "new-a" in preview
    assert "fresh-b" in preview


def test_preview_marks_new_file_against_dev_null(tmp_path: Path):
    tx = EditTransaction()
    tx.stage_write(tmp_path / "newfile.py", "x = 1\n")
    preview = tx.preview()
    assert "/dev/null" in preview


def test_preview_marks_delete_against_dev_null(tmp_path: Path):
    p = tmp_path / "drop.txt"
    p.write_text("bye\n")
    tx = EditTransaction()
    tx.stage_delete(p)
    preview = tx.preview()
    assert "-bye" in preview
    assert "/dev/null" in preview


# ─── commit ──────────────────────────────────────────────────────────────────


def test_commit_writes_all_files(tmp_path: Path):
    tx = EditTransaction()
    tx.stage_write(tmp_path / "a.txt", "alpha\n")
    tx.stage_write(tmp_path / "b.txt", "beta\n")
    result = tx.commit()
    assert (tmp_path / "a.txt").read_text() == "alpha\n"
    assert (tmp_path / "b.txt").read_text() == "beta\n"
    assert len(result.files_written) == 2
    assert result.bytes_written > 0
    assert result.duration_ms > 0


def test_commit_rejects_empty(tmp_path: Path):
    tx = EditTransaction()
    with pytest.raises(EditTransactionError, match="no ops"):
        tx.commit()


def test_commit_creates_parent_directories(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "file.txt"
    tx = EditTransaction()
    tx.stage_write(target, "hi")
    tx.commit()
    assert target.read_text() == "hi"


def test_commit_refuses_to_create_when_flag_off(tmp_path: Path):
    tx = EditTransaction()
    tx.stage(EditOp(path=tmp_path / "missing.txt", new_content="x", create_if_missing=False))
    with pytest.raises(EditTransactionError, match="does not exist"):
        tx.commit()


def test_commit_handles_delete(tmp_path: Path):
    p = tmp_path / "to-go.txt"
    p.write_text("bye\n")
    tx = EditTransaction()
    tx.stage_delete(p)
    result = tx.commit()
    assert not p.exists()
    assert str(p) in result.files_deleted


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file-mode bits aren't enforceable on Windows")
def test_commit_preserves_existing_file_mode(tmp_path: Path):
    p = tmp_path / "exec.sh"
    p.write_text("#!/bin/sh\necho hi\n")
    p.chmod(0o755)
    tx = EditTransaction()
    tx.stage_write(p, "#!/bin/sh\necho bye\n")
    tx.commit()
    mode = os.stat(p).st_mode & 0o777
    assert mode == 0o755


def test_commit_is_atomic_per_file(tmp_path: Path):
    """During commit there must never be a moment when the target file
    contains a partial write. We approximate: assert no .tmp leftover."""
    tx = EditTransaction()
    tx.stage_write(tmp_path / "x.txt", "data" * 1000)
    tx.commit()
    leftover = list(tmp_path.glob(".x.txt.*.tmp"))
    assert leftover == []


# ─── rollback ────────────────────────────────────────────────────────────────


def test_rollback_discards_staged(tmp_path: Path):
    tx = EditTransaction()
    tx.stage_write(tmp_path / "a.txt", "x")
    tx.rollback()
    assert len(tx) == 0
    assert not (tmp_path / "a.txt").exists()


def test_rollback_after_commit_raises(tmp_path: Path):
    tx = EditTransaction()
    tx.stage_write(tmp_path / "a.txt", "x")
    tx.commit()
    with pytest.raises(EditTransactionError, match="cannot rollback"):
        tx.rollback()


def test_commit_failure_restores_all_previously_written(tmp_path: Path):
    """Stage 3 ops; the third one is into a directory we'll make unwritable
    halfway through — earlier writes must be reverted."""
    a = tmp_path / "a.txt"
    a.write_text("orig-a\n")
    b = tmp_path / "b.txt"
    b.write_text("orig-b\n")
    # We'll redirect the third op into a path that fails by mocking
    # `EditOp.create_if_missing=False` for a missing nested target.
    tx = EditTransaction()
    tx.stage_write(a, "new-a\n")
    tx.stage_write(b, "new-b\n")
    tx.stage(EditOp(path=tmp_path / "no-parent" / "c.txt", new_content="x", create_if_missing=False))

    with pytest.raises(EditTransactionError):
        tx.commit()

    # a and b must be back to their original contents
    assert a.read_text() == "orig-a\n"
    assert b.read_text() == "orig-b\n"


def test_commit_failure_removes_freshly_created_files(tmp_path: Path):
    """If a NEW file was written before failure, rollback should remove it."""
    a = tmp_path / "a.txt"
    a.write_text("orig\n")
    new_file = tmp_path / "fresh.txt"
    tx = EditTransaction()
    tx.stage_write(new_file, "fresh\n")  # creates
    tx.stage(EditOp(path=tmp_path / "nope" / "x.txt", new_content="x", create_if_missing=False))
    with pytest.raises(EditTransactionError):
        tx.commit()
    assert not new_file.exists(), "freshly-created file should be removed on rollback"


# ─── encoding ────────────────────────────────────────────────────────────────


def test_commit_writes_utf8(tmp_path: Path):
    tx = EditTransaction()
    tx.stage_write(tmp_path / "u.txt", "héllo — 世界\n")
    tx.commit()
    assert (tmp_path / "u.txt").read_text(encoding="utf-8") == "héllo — 世界\n"


# ─── result shape ────────────────────────────────────────────────────────────


def test_result_distinguishes_created_vs_modified(tmp_path: Path):
    existing = tmp_path / "old.txt"
    existing.write_text("hi\n")
    tx = EditTransaction()
    tx.stage_write(existing, "bye\n")
    tx.stage_write(tmp_path / "new.txt", "fresh\n")
    result = tx.commit()
    assert str(existing) in result.files_written
    assert str(tmp_path / "new.txt") in result.files_created
