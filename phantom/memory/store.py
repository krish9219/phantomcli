"""Memory store — SQLite + FTS5 + TF-IDF rerank.

Schema:

* ``records(id, user, project, session, kind, text, created)``
* ``records_fts`` — FTS5 virtual table mirroring `text` with a
  ``content_rowid`` link.

Hybrid retrieval (BM25 + TF-IDF cosine) is implemented in pure Python:

* BM25 is delegated to FTS5 via ``ORDER BY rank``.
* TF-IDF uses the hashing trick (1024 buckets) so vocabulary growth is
  bounded.

The TF-IDF is intentionally simple. Stage 8 may swap it for a real
embedding store; the public :class:`MemoryStore` interface stays
unchanged.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["MemoryRecord", "MemoryStore"]


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HASH_BUCKETS = 1024


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _hashing_tfidf(text: str) -> dict[int, float]:
    tokens = _tokenize(text)
    if not tokens:
        return {}
    counts: Counter[int] = Counter()
    for tok in tokens:
        counts[hash(tok) % _HASH_BUCKETS] += 1
    # Naive in-document TF; corpus IDF is approximated as 1.0 at
    # encode time and applied at search time when we have a corpus
    # sample (see _query_idf). For Stage 5 we keep TF only — it's
    # surprisingly competitive on small corpora.
    norm = math.sqrt(sum(c * c for c in counts.values()))
    return {bucket: c / norm for bucket, c in counts.items()}


def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    return sum(a[k] * b[k] for k in common)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user    TEXT NOT NULL,
    project TEXT NOT NULL,
    session TEXT NOT NULL,
    kind    TEXT NOT NULL,
    text    TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_namespace
    ON records(user, project, session);

CREATE VIRTUAL TABLE IF NOT EXISTS records_fts
    USING fts5(text, content='records', content_rowid='id');

-- Keep FTS in sync.
CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, text)
        VALUES('delete', old.id, old.text);
END;
"""


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    user: str
    project: str
    session: str
    kind: str
    text: str
    created: datetime
    score: float = 0.0  # populated by search; 0 for `add`

    @classmethod
    def from_row(cls, row: sqlite3.Row, *, score: float = 0.0) -> "MemoryRecord":
        return cls(
            id=row["id"],
            user=row["user"],
            project=row["project"],
            session=row["session"],
            kind=row["kind"],
            text=row["text"],
            created=datetime.fromtimestamp(row["created"], tz=timezone.utc),
            score=score,
        )


@dataclass
class MemoryStore:
    """Hybrid lexical + TF-IDF memory store.

    Open the store with :meth:`open`. Closing is automatic at GC; for
    deterministic shutdown, call :meth:`close`.
    """

    path: Path
    _con: sqlite3.Connection = field(repr=False)

    @classmethod
    def open(cls, path: str | Path) -> "MemoryStore":
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        con = sqlite3.connect(p, isolation_level=None)  # autocommit
        con.row_factory = sqlite3.Row
        con.executescript(_SCHEMA)
        return cls(path=p, _con=con)

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass

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
        now = datetime.now(timezone.utc).timestamp()
        cur = self._con.execute(
            "INSERT INTO records(user, project, session, kind, text, created) "
            "VALUES (?,?,?,?,?,?)",
            (user, project, session, kind, text, now),
        )
        rec_id = cur.lastrowid
        return MemoryRecord(
            id=rec_id, user=user, project=project, session=session,
            kind=kind, text=text,
            created=datetime.fromtimestamp(now, tz=timezone.utc),
        )

    def delete(self, record_id: int) -> int:
        cur = self._con.execute("DELETE FROM records WHERE id = ?", (record_id,))
        return cur.rowcount

    # ─── queries ───────────────────────────────────────────────────────

    def list(
        self,
        *,
        user: str,
        project: str,
        session: str | None = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        sql = (
            "SELECT * FROM records WHERE user = ? AND project = ?"
            + (" AND session = ?" if session else "")
            + " ORDER BY id DESC LIMIT ?"
        )
        params: list = [user, project]
        if session:
            params.append(session)
        params.append(limit)
        return [MemoryRecord.from_row(r) for r in self._con.execute(sql, params)]

    def search(
        self,
        *,
        user: str,
        project: str,
        query: str,
        session: str | None = None,
        top_k: int = 5,
        bm25_weight: float = 0.6,
    ) -> list[MemoryRecord]:
        """Hybrid BM25 + TF-IDF cosine retrieval.

        The query is run against FTS5 (returns a candidate set ordered
        by BM25). We then re-rank with TF-IDF cosine and return the
        top *top_k*.

        ``bm25_weight`` (0..1) controls the blend; the cosine half is
        ``(1 - bm25_weight)``.
        """
        if not 0 <= bm25_weight <= 1:
            raise ValueError("bm25_weight must be 0..1")
        if not query.strip():
            return []
        # Sanitise for FTS5 — quote each token to neuter operators.
        tokens = _tokenize(query)
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in tokens)

        ns_clause = " AND user = ? AND project = ?"
        params: list = [fts_query, user, project]
        if session:
            ns_clause += " AND session = ?"
            params.append(session)

        # FTS5: smaller rank == better; we negate to make it descending.
        sql = (
            "SELECT records.*, bm25(records_fts) AS bm25_raw "
            "FROM records_fts JOIN records ON records.id = records_fts.rowid "
            "WHERE records_fts MATCH ?" + ns_clause
            + " ORDER BY bm25_raw LIMIT 50"
        )
        candidates = list(self._con.execute(sql, params))
        if not candidates:
            return []

        # Normalise BM25 to a [0,1]-like score (smaller raw = better).
        raws = [c["bm25_raw"] for c in candidates]
        lo, hi = min(raws), max(raws)
        rng = (hi - lo) or 1.0

        q_vec = _hashing_tfidf(query)

        scored: list[tuple[float, sqlite3.Row]] = []
        for row in candidates:
            bm25 = 1.0 - (row["bm25_raw"] - lo) / rng
            cosine = _cosine(q_vec, _hashing_tfidf(row["text"]))
            blended = bm25_weight * bm25 + (1.0 - bm25_weight) * cosine
            scored.append((blended, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            MemoryRecord.from_row(row, score=score)
            for score, row in scored[:top_k]
        ]
