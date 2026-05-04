"""Tests for :mod:`phantom.memory.store`."""

from __future__ import annotations

import pytest

from phantom.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore.open(tmp_path / "mem.db")
    yield s
    s.close()


class TestAddAndList:
    def test_round_trip(self, store):
        rec = store.add(
            user="alice", project="proj1", session="s1",
            kind="note", text="Phantom is a personal AI agent.",
        )
        assert rec.text == "Phantom is a personal AI agent."
        listed = store.list(user="alice", project="proj1")
        assert len(listed) == 1
        assert listed[0].id == rec.id

    def test_list_filters_by_namespace(self, store):
        store.add(user="alice", project="p1", session="s1", kind="n", text="A")
        store.add(user="alice", project="p2", session="s1", kind="n", text="B")
        store.add(user="bob",   project="p1", session="s1", kind="n", text="C")
        out = store.list(user="alice", project="p1")
        assert {r.text for r in out} == {"A"}

    def test_list_by_session(self, store):
        store.add(user="a", project="p", session="s1", kind="n", text="X")
        store.add(user="a", project="p", session="s2", kind="n", text="Y")
        out = store.list(user="a", project="p", session="s1")
        assert {r.text for r in out} == {"X"}

    def test_delete(self, store):
        rec = store.add(user="a", project="p", session="s", kind="n", text="X")
        n = store.delete(rec.id)
        assert n == 1
        assert store.list(user="a", project="p") == []


class TestHybridSearch:
    def test_finds_exact_match(self, store):
        store.add(user="a", project="p", session="s", kind="n",
                  text="Phantom uses bubblewrap for sandboxing")
        store.add(user="a", project="p", session="s", kind="n",
                  text="The weather is nice today")
        results = store.search(user="a", project="p", query="bubblewrap")
        assert len(results) == 1
        assert "bubblewrap" in results[0].text

    def test_ranks_more_relevant_higher(self, store):
        store.add(user="a", project="p", session="s", kind="n",
                  text="A short note about the weather.")
        store.add(user="a", project="p", session="s", kind="n",
                  text="The weather forecast says rain. Weather, weather, weather.")
        results = store.search(user="a", project="p", query="weather forecast", top_k=2)
        # Both match, but the longer + denser one ranks first.
        assert "forecast" in results[0].text

    def test_namespace_scoped_search(self, store):
        store.add(user="alice", project="p", session="s", kind="n",
                  text="alice's secret diary")
        store.add(user="bob", project="p", session="s", kind="n",
                  text="bob's secret diary")
        results = store.search(user="alice", project="p", query="diary")
        assert len(results) == 1
        assert "alice" in results[0].text

    def test_empty_query_returns_empty(self, store):
        store.add(user="a", project="p", session="s", kind="n", text="hi")
        assert store.search(user="a", project="p", query="") == []

    def test_no_corpus_returns_empty(self, store):
        assert store.search(user="a", project="p", query="anything") == []

    def test_invalid_blend_weight_raises(self, store):
        store.add(user="a", project="p", session="s", kind="n", text="hi")
        with pytest.raises(ValueError):
            store.search(user="a", project="p", query="hi", bm25_weight=1.5)

    def test_score_in_result(self, store):
        store.add(user="a", project="p", session="s", kind="n", text="alpha")
        results = store.search(user="a", project="p", query="alpha")
        assert results[0].score > 0
