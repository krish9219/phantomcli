"""Tests for :mod:`phantom.tools.fs`."""

from __future__ import annotations

import os

import pytest

from phantom.errors import PhantomError
from phantom.tools.fs import list_dir, read_file, write_file


# ─── read_file ──────────────────────────────────────────────────────────────


class TestReadFile:
    def test_round_trip(self, tmp_path):
        target = tmp_path / "note.txt"
        target.write_text("hello world")
        out = read_file(path=str(target), allowlist=(str(tmp_path),))
        assert out["ok"]
        assert out["text"] == "hello world"
        assert out["size_bytes"] == 11

    def test_missing_returns_failure(self, tmp_path):
        out = read_file(path=str(tmp_path / "absent"), allowlist=(str(tmp_path),))
        assert not out["ok"]
        assert "not found" in out["error"]

    def test_directory_rejected(self, tmp_path):
        out = read_file(path=str(tmp_path), allowlist=(str(tmp_path),))
        assert not out["ok"]
        assert "regular file" in out["error"]

    def test_outside_allowlist_rejected(self, tmp_path):
        target = tmp_path / "x"; target.write_text("x")
        out = read_file(path=str(target), allowlist=("/var/log",))
        assert not out["ok"]
        assert "allowlist" in out["error"]

    def test_symlink_outside_blocked(self, tmp_path):
        # Create allow-listed dir; place a symlink inside that points
        # OUTSIDE. Resolution should reject.
        outside = tmp_path / "secret"
        outside.write_text("password")
        allow = tmp_path / "allow"
        allow.mkdir()
        link = allow / "link.txt"
        os.symlink(outside, link)
        out = read_file(path=str(link), allowlist=(str(allow),))
        assert not out["ok"]

    def test_truncates_oversize(self, tmp_path):
        target = tmp_path / "big.txt"
        target.write_text("x" * 10_000)
        out = read_file(
            path=str(target), allowlist=(str(tmp_path),), max_bytes=4096,
        )
        assert out["ok"]
        assert out["truncated"]
        assert out["size_bytes"] == 10_000

    def test_max_bytes_floor(self, tmp_path):
        with pytest.raises(PhantomError, match="max_bytes"):
            read_file(path=str(tmp_path / "x"),
                      allowlist=(str(tmp_path),), max_bytes=10)

    def test_empty_allowlist_rejected(self, tmp_path):
        out = read_file(path=str(tmp_path / "x"), allowlist=())
        assert not out["ok"]
        assert "allowlist" in out["error"]


# ─── write_file ─────────────────────────────────────────────────────────────


class TestWriteFile:
    def test_round_trip(self, tmp_path):
        out = write_file(
            path=str(tmp_path / "out.txt"),
            text="hello",
            allowlist=(str(tmp_path),),
        )
        assert out["ok"]
        assert out["bytes_written"] == 5
        assert (tmp_path / "out.txt").read_text() == "hello"

    def test_creates_parent_dirs(self, tmp_path):
        out = write_file(
            path=str(tmp_path / "deep" / "nested" / "file.txt"),
            text="x",
            allowlist=(str(tmp_path),),
        )
        assert out["ok"]
        assert (tmp_path / "deep" / "nested" / "file.txt").exists()

    def test_outside_allowlist_rejected(self, tmp_path):
        out = write_file(
            path="/etc/shadow.bak", text="x", allowlist=(str(tmp_path),),
        )
        assert not out["ok"]


# ─── list_dir ───────────────────────────────────────────────────────────────


class TestListDir:
    def test_round_trip(self, tmp_path):
        (tmp_path / "a.txt").write_text("hi")
        (tmp_path / "b").mkdir()
        out = list_dir(path=str(tmp_path), allowlist=(str(tmp_path),))
        assert out["ok"]
        names = {e["name"]: e["kind"] for e in out["entries"]}
        # Assert containment, not equality — unrelated files dropped
        # in shared pytest tmp by other tests don't break us.
        assert names.get("a.txt") == "file"
        assert names.get("b") == "dir"

    def test_file_not_dir_rejected(self, tmp_path):
        target = tmp_path / "a.txt"; target.write_text("x")
        out = list_dir(path=str(target), allowlist=(str(tmp_path),))
        assert not out["ok"]
        assert "directory" in out["error"]

    def test_outside_allowlist_rejected(self, tmp_path):
        out = list_dir(path="/etc", allowlist=(str(tmp_path),))
        assert not out["ok"]

    def test_symlink_kind_reported(self, tmp_path):
        target = tmp_path / "real"; target.write_text("x")
        link = tmp_path / "linked"
        os.symlink(target, link)
        out = list_dir(path=str(tmp_path), allowlist=(str(tmp_path),))
        kinds = {e["name"]: e["kind"] for e in out["entries"]}
        assert kinds["linked"] == "link"
