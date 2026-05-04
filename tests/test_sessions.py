"""Tests for sessions.save/load/list/delete/export."""
from __future__ import annotations

import json
import os

import pytest

from omnicli.sessions import (
    save_session, load_session, list_sessions,
    delete_session, export_session,
)


@pytest.fixture
def sess_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    monkeypatch.setenv("PHANTOM_SESSIONS_DIR", str(d))
    return d


@pytest.fixture
def ctx():
    return {
        "messages": [
            {"role": "system",    "content": "You are Phantom."},
            {"role": "user",      "content": "Hello"},
            {"role": "assistant", "content": "Hi there."},
        ],
        "tools": [
            {"tool": "run_bash", "ok": True},
            {"tool": "write_file", "ok": True},
        ],
    }


class TestSaveLoad:
    def test_save_returns_id(self, sess_dir, ctx):
        sid = save_session(ctx, name="first")
        assert isinstance(sid, str) and len(sid) == 12

    def test_save_creates_file_on_disk(self, sess_dir, ctx):
        sid = save_session(ctx)
        path = sess_dir / sid / "session.json"
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["id"] == sid
        assert len(data["messages"]) == 3

    def test_load_roundtrip(self, sess_dir, ctx):
        sid = save_session(ctx, name="demo")
        loaded = load_session(sid)
        assert loaded is not None
        assert loaded["id"] == sid
        assert loaded["name"] == "demo"
        assert loaded["messages"] == ctx["messages"]
        assert loaded["tools"] == ctx["tools"]

    def test_load_missing_returns_none(self, sess_dir):
        assert load_session("deadbeefdead") is None

    def test_saves_config_snapshot(self, sess_dir, ctx):
        from omnicli.memory import save_config
        save_config("main_model", "claude-opus-4-5")
        sid = save_session(ctx)
        loaded = load_session(sid)
        assert loaded["config"]["main_model"] == "claude-opus-4-5"

    def test_saves_timestamps(self, sess_dir, ctx):
        sid = save_session(ctx)
        loaded = load_session(sid)
        assert loaded["created"]
        assert loaded["updated"]


class TestSaveOverwrite:
    def test_save_with_same_id_overwrites(self, sess_dir, ctx):
        sid = save_session(ctx, name="v1")
        ctx2 = dict(ctx)
        ctx2["messages"] = ctx["messages"] + [{"role": "user", "content": "more"}]
        sid2 = save_session(ctx2, name="v2", sid=sid)
        assert sid2 == sid
        loaded = load_session(sid)
        assert loaded["name"] == "v2"
        assert len(loaded["messages"]) == 4

    def test_overwrite_preserves_created_timestamp(self, sess_dir, ctx):
        sid = save_session(ctx)
        original = load_session(sid)["created"]
        # Overwrite with a different ctx
        save_session(ctx, sid=sid)
        again = load_session(sid)
        # created should remain stable; updated should move
        assert again["created"] == original


class TestList:
    def test_list_empty(self, sess_dir):
        assert list_sessions() == []

    def test_list_multiple(self, sess_dir, ctx):
        sid1 = save_session(ctx, name="a")
        sid2 = save_session(ctx, name="b")
        rows = list_sessions()
        assert len(rows) == 2
        ids = {r["id"] for r in rows}
        assert ids == {sid1, sid2}

    def test_list_newest_first(self, sess_dir, ctx):
        import time
        sid1 = save_session(ctx, name="old")
        time.sleep(1.1)  # bump second-resolution timestamp
        sid2 = save_session(ctx, name="new")
        rows = list_sessions()
        assert rows[0]["id"] == sid2
        assert rows[1]["id"] == sid1

    def test_list_includes_counts(self, sess_dir, ctx):
        sid = save_session(ctx)
        row = list_sessions()[0]
        assert row["messages"] == 3
        assert row["tools"] == 2


class TestDelete:
    def test_delete_existing(self, sess_dir, ctx):
        sid = save_session(ctx)
        assert delete_session(sid) is True
        assert load_session(sid) is None

    def test_delete_missing_returns_false(self, sess_dir):
        assert delete_session("nosuchid") is False

    def test_delete_removes_dir(self, sess_dir, ctx):
        sid = save_session(ctx)
        delete_session(sid)
        assert not (sess_dir / sid).exists()


class TestExport:
    def test_export_copies_file(self, sess_dir, ctx, tmp_path):
        sid = save_session(ctx, name="mysession")
        target = tmp_path / "exported.json"
        out = export_session(sid, str(target))
        assert out == str(target)
        data = json.loads(target.read_text())
        assert data["name"] == "mysession"

    def test_export_missing_returns_none(self, sess_dir, tmp_path):
        assert export_session("nope", str(tmp_path / "x.json")) is None


class TestRobustness:
    def test_malformed_session_json_ignored_by_list(self, sess_dir):
        """A broken session.json on disk should not crash list_sessions."""
        bad_dir = sess_dir / "brokenid"
        bad_dir.mkdir(parents=True)
        (bad_dir / "session.json").write_text("{not json")
        # Plus a good one
        save_session({"messages": [{"role": "user", "content": "hi"}]})
        rows = list_sessions()
        assert len(rows) == 1  # broken one filtered out

    def test_atomic_write_does_not_leave_tmp(self, sess_dir, ctx):
        sid = save_session(ctx)
        d = sess_dir / sid
        assert (d / "session.json").is_file()
        # No stale .tmp file on disk
        tmps = [f for f in os.listdir(d) if f.endswith(".tmp")]
        assert tmps == []
