"""Phantom memory v2 — hybrid lexical + lexical-vector retrieval.

Stage 5 ships:

* :class:`MemoryStore` — SQLite + FTS5 + a lightweight TF-IDF
  cosine-similarity reranker. The "vector" half is a hand-rolled
  hashing-trick TF-IDF, deliberately small — no torch / sentence-
  transformers dependency, runs anywhere Python runs. Operators who
  want true embedding vectors plug a different store via the same
  interface in Stage 8.
* :class:`MemoryRecord` — one stored item.
* Namespaces — every record belongs to ``(user, project, session)``;
  retrieval is namespace-scoped.

The hybrid retrieval algorithm:

1. FTS5 BM25 for lexical recall.
2. TF-IDF cosine on the same candidate set for semantic boost.
3. Linear blend: ``score = 0.6 * bm25_norm + 0.4 * cosine``.
4. Top-K returned to the caller.
"""

from __future__ import annotations

from phantom.memory.store import MemoryRecord, MemoryStore

__all__ = ["MemoryRecord", "MemoryStore"]
