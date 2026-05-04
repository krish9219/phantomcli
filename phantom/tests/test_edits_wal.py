"""Tests for the edit-transaction write-ahead log + crash recovery."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from phantom.edits import EditTransaction
from phantom.edits.wal import (
    WAL_VERSION,
    WalEntry,
    WalRecord,
    delete_wal,
    list_pending,
    mark_committed,
    make_txn_id,
    recover_pending,
    write_wal,
)


@pytest.fixture
def wal_dir(tmp_path: Path) -> Path:
    d = tmp_path / "wal"
    d.mkdir()
    return d


# ─── WAL record serialization ───────────────────────────────────────────────


def test_walrecord_roundtrip():
    rec = WalRecord(
        txn_id="t1",
        started_at=1000.0,
        description="test",
        entries=(
            WalEntry(path="/x", body_b64="aGk=", mode=0o644, existed=True),
            WalEntry(path="/y", body_b64=None, mode=None, existed=False),
        ),
    )
    raw = rec.to_json()
    parsed = WalRecord.from_json(raw)
    assert parsed.txn_id == "t1"
    assert parsed.started_at == 1000.0
    assert parsed.description == "test"
    assert len(parsed.entries) == 2
    assert parsed.entries[0].body_b64 == "aGk="
    assert parsed.entries[1].body_b64 is None


def test_walrecord_rejects_unknown_version():
    raw = json.dumps({"version": 999, "txn_id": "t", "started_at": 0, "entries": []})
    with pytest.raises(ValueError, match="version"):
        WalRecord.from_json(raw)


def test_wal_version_constant_is_one():
    assert WAL_VERSION == 1


# ─── on-disk WAL primitives ─────────────────────────────────────────────────


def test_write_wal_creates_owner_only_file(wal_dir: Path):
    rec = WalRecord(txn_id=make_txn_id(), started_at=0, entries=())
    path = write_wal(rec, wal_dir=wal_dir)
    assert path.exists()
    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o600


def test_write_wal_atomic_no_tmp_left(wal_dir: Path):
    rec = WalRecord(txn_id=make_txn_id(), started_at=0, entries=())
    write_wal(rec, wal_dir=wal_dir)
    leftover = list(wal_dir.glob("*.tmp"))
    assert leftover == []


def test_mark_committed_renames_extension(wal_dir: Path):
    rec = WalRecord(txn_id="abc", started_at=0, entries=())
    p = write_wal(rec, wal_dir=wal_dir)
    mark_committed(p)
    assert not p.exists()
    assert p.with_suffix(".committed").exists()


def test_delete_wal_removes_both_extensions(wal_dir: Path):
    rec = WalRecord(txn_id="abc", started_at=0, entries=())
    p = write_wal(rec, wal_dir=wal_dir)
    mark_committed(p)
    delete_wal(p)
    assert not p.exists()
    assert not p.with_suffix(".committed").exists()


# ─── pending detection ──────────────────────────────────────────────────────


def test_list_pending_empty_dir(wal_dir: Path):
    assert list_pending(wal_dir) == []


def test_list_pending_finds_uncommitted(wal_dir: Path):
    p1 = write_wal(WalRecord(txn_id="a", started_at=0, entries=()), wal_dir=wal_dir)
    p2 = write_wal(WalRecord(txn_id="b", started_at=0, entries=()), wal_dir=wal_dir)
    mark_committed(p2)
    pending = list_pending(wal_dir)
    assert len(pending) == 1
    assert pending[0].name.startswith("a")


# ─── recovery ───────────────────────────────────────────────────────────────


def test_recover_restores_uncommitted_file(wal_dir: Path, tmp_path: Path):
    target = tmp_path / "restore-me.txt"
    original = b"original content"
    write_wal(
        WalRecord(
            txn_id="r1",
            started_at=0,
            entries=(
                WalEntry(
                    path=str(target),
                    body_b64=base64.b64encode(original).decode("ascii"),
                    mode=0o644,
                    existed=True,
                ),
            ),
        ),
        wal_dir=wal_dir,
    )
    # Simulate the target having been overwritten by a partial commit.
    target.write_bytes(b"corrupted")
    summaries = recover_pending(wal_dir)
    assert len(summaries) == 1
    assert "recovered 1" in summaries[0][1]
    assert target.read_bytes() == original


def test_recover_removes_freshly_created_file(wal_dir: Path, tmp_path: Path):
    target = tmp_path / "should-not-exist.txt"
    target.write_text("created mid-commit")
    write_wal(
        WalRecord(
            txn_id="r2",
            started_at=0,
            entries=(
                WalEntry(path=str(target), body_b64=None, mode=None, existed=False),
            ),
        ),
        wal_dir=wal_dir,
    )
    recover_pending(wal_dir)
    assert not target.exists()


def test_recover_skips_committed(wal_dir: Path):
    p = write_wal(
        WalRecord(txn_id="committed-one", started_at=0, entries=()),
        wal_dir=wal_dir,
    )
    mark_committed(p)
    summaries = recover_pending(wal_dir)
    assert summaries == []


def test_recover_handles_corrupt_wal(wal_dir: Path):
    bad = wal_dir / "bad.wal"
    bad.write_text("not json {")
    summaries = recover_pending(wal_dir)
    assert len(summaries) == 1
    assert "CORRUPT" in summaries[0][1]
    # Corrupt WAL stays for manual inspection; do not silently delete.
    assert bad.exists()


def test_recover_preserves_file_mode(wal_dir: Path, tmp_path: Path):
    target = tmp_path / "exec.sh"
    target.write_bytes(b"#!/bin/sh\necho hi\n")
    target.chmod(0o755)
    original = target.read_bytes()
    target.write_bytes(b"corrupted")
    write_wal(
        WalRecord(
            txn_id="mode-r",
            started_at=0,
            entries=(
                WalEntry(
                    path=str(target),
                    body_b64=base64.b64encode(original).decode("ascii"),
                    mode=0o755,
                    existed=True,
                ),
            ),
        ),
        wal_dir=wal_dir,
    )
    recover_pending(wal_dir)
    assert target.read_bytes() == original
    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o755


# ─── EditTransaction integration ───────────────────────────────────────────


def test_transaction_with_wal_enabled_writes_wal_during_commit(
    wal_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PHANTOM_HOME", str(wal_dir.parent))
    monkeypatch.setattr("phantom.edits.wal.default_wal_dir", lambda: wal_dir)
    a = tmp_path / "a.txt"
    a.write_text("orig\n")
    tx = EditTransaction(wal_enabled=True, wal_description="test commit")
    tx.stage_write(a, "new\n")
    tx.commit()
    # On clean exit the WAL is deleted entirely.
    assert list(wal_dir.glob("*.wal")) == []
    assert list(wal_dir.glob("*.committed")) == []
    assert a.read_text() == "new\n"


def test_transaction_failed_commit_drops_wal(
    wal_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """If commit raises, the WAL must be cleaned so we don't trigger a
    spurious recovery on next start (in-memory rollback already restored)."""
    monkeypatch.setattr("phantom.edits.wal.default_wal_dir", lambda: wal_dir)
    a = tmp_path / "a.txt"
    a.write_text("orig\n")
    tx = EditTransaction(wal_enabled=True)
    tx.stage_write(a, "new\n")
    # Force the second stage to fail by pointing into a non-creatable dir
    from phantom.edits import EditOp
    tx.stage(EditOp(path=tmp_path / "nope" / "x.txt", new_content="x", create_if_missing=False))
    with pytest.raises(Exception):
        tx.commit()
    # WAL dropped; no pending recovery.
    assert list(wal_dir.glob("*.wal")) == []
    # Original a.txt restored.
    assert a.read_text() == "orig\n"


def test_transaction_default_no_wal(wal_dir: Path, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch):
    """WAL must be opt-in — default behaviour stays cheap."""
    monkeypatch.setattr("phantom.edits.wal.default_wal_dir", lambda: wal_dir)
    tx = EditTransaction()
    tx.stage_write(tmp_path / "x.txt", "hi")
    tx.commit()
    assert list(wal_dir.glob("*")) == []
