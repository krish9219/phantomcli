"""Tests for agent_scratchpad — SQLite kv for cross-agent handoff."""
from __future__ import annotations

import os
import threading
import time

import pytest

from omnicli import agent_scratchpad as sp


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_SCRATCHPAD_DB", str(tmp_path / "scratch.db"))
    yield


class TestPutGet:
    def test_put_then_get_roundtrip(self):
        sp.put("s1", "a1", "key1", "value1")
        assert sp.get("s1", "a1", "key1") == "value1"

    def test_get_missing_returns_none(self):
        assert sp.get("s1", "a1", "nope") is None

    def test_put_updates_existing(self):
        sp.put("s1", "a1", "k", "first")
        sp.put("s1", "a1", "k", "second")
        assert sp.get("s1", "a1", "k") == "second"

    def test_preserves_created_on_update(self):
        sp.put("s1", "a1", "k", "v1")
        rows = sp.get_all("s1")
        created_first = rows[0].created
        time.sleep(0.02)
        sp.put("s1", "a1", "k", "v2")
        rows = sp.get_all("s1")
        assert rows[0].created == created_first
        assert rows[0].updated >= created_first


class TestScopes:
    def test_agents_are_isolated(self):
        sp.put("s1", "agent1", "k", "from-1")
        sp.put("s1", "agent2", "k", "from-2")
        assert sp.get("s1", "agent1", "k") == "from-1"
        assert sp.get("s1", "agent2", "k") == "from-2"

    def test_sessions_are_isolated(self):
        sp.put("session-A", "a", "k", "a-val")
        sp.put("session-B", "a", "k", "b-val")
        assert sp.get("session-A", "a", "k") == "a-val"
        assert sp.get("session-B", "a", "k") == "b-val"

    def test_get_all_scoped_to_session(self):
        sp.put("s1", "a", "k1", "v")
        sp.put("s1", "b", "k2", "v")
        sp.put("s2", "a", "k3", "v")
        rows = sp.get_all("s1")
        assert len(rows) == 2
        assert {r.key for r in rows} == {"k1", "k2"}


class TestPeek:
    def test_glob_agent_filter(self):
        sp.put("s1", "fetcher", "status", "ok")
        sp.put("s1", "backend", "status", "ok")
        sp.put("s1", "frontend", "status", "ok")
        rows = sp.peek("s1", agent_glob="f*")
        agents = {r.agent_id for r in rows}
        assert agents == {"fetcher", "frontend"}

    def test_glob_key_filter(self):
        sp.put("s1", "a", "cache_size", "42")
        sp.put("s1", "a", "log_level", "info")
        sp.put("s1", "a", "cache_ttl",  "60")
        rows = sp.peek("s1", key_glob="cache_*")
        keys = {r.key for r in rows}
        assert keys == {"cache_size", "cache_ttl"}

    def test_glob_both_filters(self):
        sp.put("s1", "agent-x", "k1", "v")
        sp.put("s1", "agent-y", "k2", "v")
        rows = sp.peek("s1", agent_glob="agent-x", key_glob="k*")
        assert len(rows) == 1
        assert rows[0].key == "k1"


class TestDelete:
    def test_delete_specific_key(self):
        sp.put("s1", "a", "k1", "v")
        sp.put("s1", "a", "k2", "v")
        assert sp.delete("s1", "a", "k1") == 1
        assert sp.get("s1", "a", "k1") is None
        assert sp.get("s1", "a", "k2") == "v"

    def test_delete_by_agent(self):
        sp.put("s1", "a", "k1", "v")
        sp.put("s1", "a", "k2", "v")
        sp.put("s1", "b", "k3", "v")
        assert sp.delete("s1", "a") == 2
        assert sp.get("s1", "b", "k3") == "v"

    def test_delete_whole_session(self):
        sp.put("s1", "a", "k1", "v")
        sp.put("s1", "b", "k2", "v")
        sp.put("s2", "a", "k3", "v")
        assert sp.delete("s1") == 2
        assert sp.get("s2", "a", "k3") == "v"


class TestTtl:
    def test_expired_row_returns_none(self):
        sp.put("s1", "a", "k", "v", ttl_s=0.05)
        assert sp.get("s1", "a", "k") == "v"
        time.sleep(0.1)
        assert sp.get("s1", "a", "k") is None

    def test_expired_excluded_from_get_all(self):
        sp.put("s1", "a", "k1", "v", ttl_s=0.05)
        sp.put("s1", "a", "k2", "v")  # no TTL — lives forever
        time.sleep(0.1)
        rows = sp.get_all("s1")
        assert len(rows) == 1
        assert rows[0].key == "k2"

    def test_cleanup_expired_removes_rows(self):
        sp.put("s1", "a", "k1", "v", ttl_s=0.05)
        sp.put("s1", "a", "k2", "v")
        time.sleep(0.1)
        removed = sp.cleanup_expired()
        assert removed == 1
        rows = sp.get_all("s1")
        assert len(rows) == 1

    def test_no_ttl_never_expires(self):
        sp.put("s1", "a", "k", "v")
        time.sleep(0.1)
        assert sp.get("s1", "a", "k") == "v"


class TestConcurrency:
    def test_parallel_writes_serialized(self):
        """50 threads each writing a distinct key in the same session
        should all succeed without deadlock."""
        def _worker(i):
            sp.put("s1", f"agent-{i}", "ok", str(i))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(50)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        rows = sp.get_all("s1")
        assert len(rows) == 50

    def test_read_sees_write(self):
        sp.put("s1", "a", "k", "v1")
        # Same-thread read-your-write
        assert sp.get("s1", "a", "k") == "v1"


class TestLargeValues:
    def test_handles_largish_value(self):
        big = "x" * 100_000
        sp.put("s1", "a", "k", big)
        assert sp.get("s1", "a", "k") == big
