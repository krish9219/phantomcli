"""Vector-embedding memory backend.

Drop-in alternative to :class:`phantom.memory.MemoryStore`'s SQLite +
TF-IDF hybrid. Closes the "TF-IDF buckets collide on >10k records"
risk flagged in the Stage-5 peer review by using a real embedding
model.

Implementation: a thin adapter over ``chromadb`` (``[vector]`` extra).
We pick chromadb because:

* It bundles a small default embedder (all-MiniLM-L6-v2, 90 MB) so
  operators don't have to wire a separate embedding service.
* It runs in-process — no separate daemon.
* Persistent storage is a single directory under ``$PHANTOM_HOME``.
* The Apache-2.0 licence aligns with our open-core surface.

Public API mirrors :class:`MemoryStore` exactly:

* :meth:`open` factory.
* :meth:`add` / :meth:`delete` / :meth:`list` / :meth:`search`.
* Same :class:`MemoryRecord` returned by every query method.

Operators flip backends per session via the chosen ``MemoryStore`` import:

::

    # default (FTS5 + TF-IDF):
    from phantom.memory import MemoryStore
    store = MemoryStore.open(...)

    # vector:
    from phantom.memory.vector_store import VectorMemoryStore
    store = VectorMemoryStore.open(...)

The agent loop and tools never see the difference — the public method
signatures match.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phantom.errors import PhantomError
from phantom.memory.store import MemoryRecord

__all__ = ["VectorMemoryStore"]

log = logging.getLogger(__name__)


def _namespace_filter(
    user: str, project: str, session: str | None,
) -> dict[str, Any]:
    """Build a chromadb ``where`` filter for the namespace.

    chromadb's where DSL accepts equality comparisons combined with
    ``$and``. We always include user + project; session is optional.
    """
    clauses: list[dict[str, Any]] = [
        {"user": {"$eq": user}},
        {"project": {"$eq": project}},
    ]
    if session:
        clauses.append({"session": {"$eq": session}})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


@dataclass
class VectorMemoryStore:
    """chromadb-backed memory store.

    Open with :meth:`open`. The constructor takes a chromadb client
    and a collection — tests inject fakes; production calls
    :meth:`open` which builds them.
    """

    path: Path
    _client: Any = field(repr=False)
    _collection: Any = field(repr=False)

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        collection_name: str = "phantom_memory",
        client: Any = None,
        embedding_function: Any = None,
    ) -> "VectorMemoryStore":
        """Open a persistent chromadb collection.

        Parameters
        ----------
        path:
            Directory the chromadb persistent store lives in. Created
            with mode 0700 if missing.
        collection_name:
            chromadb collection name. Default ``phantom_memory``.
        client:
            Override for tests. If supplied, ``path`` is ignored.
        embedding_function:
            Optional explicit embedding function. ``None`` lets
            chromadb pick its default (sentence-transformers MiniLM).
        """
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        if client is None:
            try:
                import chromadb  # type: ignore[import-not-found]
            except ImportError as exc:
                raise PhantomError(
                    "chromadb is not installed; "
                    "install via `pip install phantom-cli[vector]`."
                ) from exc
            client = chromadb.PersistentClient(path=str(target))
        # get_or_create works whether the collection exists or not.
        kwargs: dict[str, Any] = {"name": collection_name}
        if embedding_function is not None:
            kwargs["embedding_function"] = embedding_function
        collection = client.get_or_create_collection(**kwargs)
        return cls(path=target, _client=client, _collection=collection)

    def close(self) -> None:
        """No-op for chromadb persistent clients (auto-flush)."""
        return None

    # ─── writes ────────────────────────────────────────────────────────

    def add(
        self,
        *,
        user: str,
        project: str,
        session: str,
        kind: str,
        text: str,
    ) -> MemoryRecord:
        rec_id = str(uuid.uuid4())
        now = time.time()
        metadata = {
            "user": user,
            "project": project,
            "session": session,
            "kind": kind,
            "created": now,
        }
        try:
            self._collection.add(
                ids=[rec_id],
                documents=[text],
                metadatas=[metadata],
            )
        except Exception as exc:
            raise PhantomError(f"vector add failed: {exc}") from exc
        return MemoryRecord(
            id=_stable_int_id(rec_id),
            user=user, project=project, session=session,
            kind=kind, text=text,
            created=datetime.fromtimestamp(now, tz=timezone.utc),
        )

    def delete(self, record_id: int) -> int:
        """Delete by the ``id`` field of a :class:`MemoryRecord`.

        Note: chromadb keys are UUIDs; we keep a ``int → uuid``
        translation cache populated on every read. Callers that
        survived a process restart need to query again to refresh
        the cache.
        """
        chroma_id = _LOCAL_ID_CACHE.get(record_id)
        if chroma_id is None:
            return 0
        try:
            self._collection.delete(ids=[chroma_id])
            _LOCAL_ID_CACHE.pop(record_id, None)
            return 1
        except Exception as exc:
            raise PhantomError(f"vector delete failed: {exc}") from exc

    # ─── queries ───────────────────────────────────────────────────────

    def list(
        self,
        *,
        user: str,
        project: str,
        session: str | None = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        try:
            result = self._collection.get(
                where=_namespace_filter(user, project, session),
                limit=limit,
            )
        except Exception as exc:
            raise PhantomError(f"vector list failed: {exc}") from exc
        return _records_from_get(result)

    def search(
        self,
        *,
        user: str,
        project: str,
        query: str,
        session: str | None = None,
        top_k: int = 5,
        bm25_weight: float = 0.6,  # noqa: ARG002 — accepted for API parity
    ) -> list[MemoryRecord]:
        if not query.strip():
            return []
        try:
            result = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=_namespace_filter(user, project, session),
            )
        except Exception as exc:
            raise PhantomError(f"vector search failed: {exc}") from exc
        return _records_from_query(result)


# ─── id translation ─────────────────────────────────────────────────────────

# chromadb uses UUID strings; MemoryRecord.id is an int. We store the
# mapping in a process-local dict so deletes can round-trip. Operators
# who restart the process and want to delete an old record query first
# (which repopulates the cache).

_LOCAL_ID_CACHE: dict[int, str] = {}


def _stable_int_id(uuid_str: str) -> int:
    """Stable hash of a UUID into a Python int. Cached so the same UUID
    always returns the same int within a process."""
    out = abs(hash(uuid_str)) & 0xFFFFFFFFFFFFFFFF
    _LOCAL_ID_CACHE[out] = uuid_str
    return out


def _records_from_get(result: dict[str, Any]) -> list[MemoryRecord]:
    ids = result.get("ids") or []
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []
    out: list[MemoryRecord] = []
    for i, chroma_id in enumerate(ids):
        text = docs[i] if i < len(docs) else ""
        meta = metas[i] if i < len(metas) else {}
        out.append(_record(chroma_id, text, meta, score=0.0))
    return out


def _records_from_query(result: dict[str, Any]) -> list[MemoryRecord]:
    # Query results come back as lists-of-lists (one inner list per query).
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    out: list[MemoryRecord] = []
    for i, chroma_id in enumerate(ids):
        text = docs[i] if i < len(docs) else ""
        meta = metas[i] if i < len(metas) else {}
        # Chroma returns squared L2 distance for the default
        # embedding; smaller is more similar. We invert + clamp into
        # a [0, 1] score so callers can use it the same way the
        # FTS5+TF-IDF score is used.
        dist = float(distances[i]) if i < len(distances) else 0.0
        score = max(0.0, 1.0 - min(dist, 1.0))
        out.append(_record(chroma_id, text, meta, score=score))
    return out


def _record(chroma_id: str, text: str, meta: dict[str, Any], *, score: float) -> MemoryRecord:
    created = float(meta.get("created", time.time()))
    return MemoryRecord(
        id=_stable_int_id(chroma_id),
        user=str(meta.get("user", "")),
        project=str(meta.get("project", "")),
        session=str(meta.get("session", "")),
        kind=str(meta.get("kind", "note")),
        text=text,
        created=datetime.fromtimestamp(created, tz=timezone.utc),
        score=score,
    )
