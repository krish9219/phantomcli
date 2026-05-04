"""Tests for :mod:`phantom.memory.vector_store`.

Uses a fake chromadb client so the suite doesn't need to download the
embedding model. A separate gated test (PHANTOM_VECTOR_REAL=1) drives
real chromadb.
"""

from __future__ import annotations

import os
import time

import pytest

from phantom.errors import PhantomError
from phantom.memory.store import MemoryRecord
from phantom.memory.vector_store import VectorMemoryStore, _stable_int_id


# ─── fake chromadb client ───────────────────────────────────────────────────


class _FakeCollection:
    """Stand-in for chromadb.Collection. Stores records in a dict."""

    def __init__(self):
        self._records: dict[str, dict] = {}

    def add(self, *, ids, documents, metadatas):
        for i, doc, meta in zip(ids, documents, metadatas, strict=True):
            self._records[i] = {"document": doc, "metadata": dict(meta)}

    def delete(self, *, ids):
        for i in ids:
            self._records.pop(i, None)

    def get(self, *, where, limit=100):
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        for i, r in list(self._records.items())[:limit]:
            if not _matches(r["metadata"], where):
                continue
            ids.append(i); docs.append(r["document"]); metas.append(r["metadata"])
        return {"ids": ids, "documents": docs, "metadatas": metas}

    def query(self, *, query_texts, n_results, where):
        # Naive matcher: rank by text overlap with the first query.
        q = (query_texts[0] if query_texts else "").lower().split()
        scored: list[tuple[float, str]] = []
        for i, r in self._records.items():
            if not _matches(r["metadata"], where):
                continue
            d = r["document"].lower()
            overlap = sum(1 for tok in q if tok in d)
            # Distance = 1 - normalised overlap, clamped to [0, 1].
            denom = max(1, len(q))
            distance = max(0.0, min(1.0, 1.0 - overlap / denom))
            scored.append((distance, i))
        scored.sort()
        scored = scored[:n_results]

        ids: list[str] = []; docs: list[str] = []
        metas: list[dict] = []; dists: list[float] = []
        for distance, i in scored:
            ids.append(i)
            docs.append(self._records[i]["document"])
            metas.append(self._records[i]["metadata"])
            dists.append(distance)
        return {
            "ids": [ids], "documents": [docs],
            "metadatas": [metas], "distances": [dists],
        }


def _matches(meta: dict, where: dict) -> bool:
    """Tiny chromadb where DSL evaluator. Supports ``$and`` + ``$eq``."""
    if "$and" in where:
        return all(_matches(meta, c) for c in where["$and"])
    for key, expr in where.items():
        if isinstance(expr, dict) and "$eq" in expr:
            if meta.get(key) != expr["$eq"]:
                return False
        else:
            if meta.get(key) != expr:
                return False
    return True


class _FakeClient:
    def __init__(self):
        self._collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(self, *, name, **_kwargs):
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]


@pytest.fixture
def store(tmp_path):
    return VectorMemoryStore.open(
        tmp_path / "vec",
        client=_FakeClient(),
    )


# ─── add / list ──────────────────────────────────────────────────────────────


class TestAddList:
    def test_add_returns_record(self, store):
        rec = store.add(
            user="u", project="p", session="s",
            kind="note", text="hello world",
        )
        assert isinstance(rec, MemoryRecord)
        assert rec.text == "hello world"
        assert rec.user == "u"

    def test_list_filters_by_namespace(self, store):
        store.add(user="alice", project="p", session="s",
                  kind="n", text="A")
        store.add(user="bob",   project="p", session="s",
                  kind="n", text="B")
        out = store.list(user="alice", project="p")
        assert {r.text for r in out} == {"A"}

    def test_list_by_session(self, store):
        store.add(user="u", project="p", session="s1", kind="n", text="X")
        store.add(user="u", project="p", session="s2", kind="n", text="Y")
        out = store.list(user="u", project="p", session="s1")
        assert {r.text for r in out} == {"X"}


# ─── search ──────────────────────────────────────────────────────────────────


class TestSearch:
    def test_finds_relevant(self, store):
        store.add(user="u", project="p", session="s", kind="n",
                  text="Phantom uses bubblewrap for sandboxing")
        store.add(user="u", project="p", session="s", kind="n",
                  text="The cake is a lie")
        results = store.search(user="u", project="p", query="bubblewrap")
        assert len(results) >= 1
        assert "bubblewrap" in results[0].text

    def test_empty_query_returns_empty(self, store):
        store.add(user="u", project="p", session="s", kind="n", text="hi")
        assert store.search(user="u", project="p", query="") == []

    def test_namespace_isolation_in_search(self, store):
        store.add(user="alice", project="p", session="s",
                  kind="n", text="alice secret")
        store.add(user="bob",   project="p", session="s",
                  kind="n", text="bob secret")
        results = store.search(user="alice", project="p", query="secret")
        assert all(r.user == "alice" for r in results)

    def test_score_in_result(self, store):
        store.add(user="u", project="p", session="s", kind="n",
                  text="alpha beta gamma")
        out = store.search(user="u", project="p", query="alpha")
        assert out
        assert 0.0 <= out[0].score <= 1.0


# ─── delete ──────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_removes(self, store):
        rec = store.add(
            user="u", project="p", session="s",
            kind="n", text="to be deleted",
        )
        n = store.delete(rec.id)
        assert n == 1
        assert store.list(user="u", project="p") == []

    def test_delete_unknown_returns_zero(self, store):
        # An ID not in the cache is silently a no-op.
        assert store.delete(999_999_999) == 0


# ─── id translation ─────────────────────────────────────────────────────────


class TestIdTranslation:
    def test_stable(self):
        a = _stable_int_id("uuid-abc")
        b = _stable_int_id("uuid-abc")
        assert a == b

    def test_different_uuids_different_ints(self):
        # Hash collision is theoretically possible but vanishingly
        # unlikely for two random UUIDs.
        a = _stable_int_id("uuid-1")
        b = _stable_int_id("uuid-2")
        assert a != b


# ─── error path: chromadb exception wrapped ─────────────────────────────────


class TestErrorWrapping:
    def test_add_failure_wrapped(self, tmp_path):
        class _Boom(_FakeCollection):
            def add(self, **_kwargs):
                raise RuntimeError("disk full")

        client = _FakeClient()
        client._collections["phantom_memory"] = _Boom()
        store = VectorMemoryStore.open(tmp_path / "v", client=client)
        with pytest.raises(PhantomError, match="vector add failed"):
            store.add(user="u", project="p", session="s",
                       kind="n", text="x")


# ─── real-chromadb gated test ───────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("PHANTOM_VECTOR_REAL"),
    reason="set PHANTOM_VECTOR_REAL=1 to download the embedding model and run",
)
class TestRealChromadb:
    def test_round_trip(self, tmp_path):
        # No client= override; this exercises the real chromadb path.
        store = VectorMemoryStore.open(tmp_path / "real")
        store.add(user="u", project="p", session="s",
                  kind="n", text="Phantom sandboxes shell calls.")
        store.add(user="u", project="p", session="s",
                  kind="n", text="The weather is great.")
        results = store.search(user="u", project="p",
                                query="sandboxing security")
        assert len(results) >= 1
        # Top result should mention the sandbox topic.
        assert "sandbox" in results[0].text.lower()
