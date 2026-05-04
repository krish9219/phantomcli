"""
Shared scratchpad for parallel subagents — a SQLite-backed kv store
scoped by session that lets upstream agents publish partial results
downstream agents can read without re-running expensive prompts.

This is a Phantom advantage over Claude Code's Agent tool: CC subagents
can only exchange information via their final text answer. Phantom's
`AgentOrchestrator` already runs dependency waves; with a shared
scratchpad, wave-2 agents can read wave-1's outputs mid-run and build
on them.

Schema:
    scratch(session_id TEXT, agent_id TEXT, key TEXT,
            value TEXT, created REAL, updated REAL, ttl_s REAL,
            PRIMARY KEY (session_id, agent_id, key))

API:
  * put(session, agent, key, value, ttl_s=None)
  * get(session, agent, key)                       → str | None
  * get_all(session)                               → list[{agent, key, value}]
  * peek(session, agent_glob, key_glob)            → list[rows]
  * delete(session, agent=None, key=None)
  * cleanup_expired(now=None)                      → int rows deleted

Concurrency: SQLite with WAL mode + explicit transactions is safe for the
read-mostly / occasional-write pattern multi-agent orchestration creates.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

log = logging.getLogger("omnicli.agent_scratchpad")

_DEFAULT_DB = os.path.expanduser("~/.phantom/scratchpad.db")
_lock = threading.Lock()


def _db_path() -> str:
    return os.environ.get("PHANTOM_SCRATCHPAD_DB", _DEFAULT_DB)


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scratch (
    session_id TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    created    REAL NOT NULL,
    updated    REAL NOT NULL,
    ttl_s      REAL,
    PRIMARY KEY (session_id, agent_id, key)
);
CREATE INDEX IF NOT EXISTS scratch_session_idx ON scratch(session_id);
CREATE INDEX IF NOT EXISTS scratch_ttl_idx ON scratch(ttl_s);
"""


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    _ensure_dir(path)
    c = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(_SCHEMA)
        yield c
    finally:
        c.close()


@dataclass
class ScratchRow:
    session_id: str
    agent_id:   str
    key:        str
    value:      str
    created:    float
    updated:    float
    ttl_s:      Optional[float] = None


def put(session_id: str, agent_id: str, key: str, value: str,
        ttl_s: Optional[float] = None) -> None:
    now = time.time()
    with _lock, _conn() as c:
        # Preserve original created timestamp on update
        row = c.execute(
            "SELECT created FROM scratch WHERE session_id=? AND agent_id=? AND key=?",
            (session_id, agent_id, key),
        ).fetchone()
        created = row[0] if row else now
        c.execute(
            "INSERT OR REPLACE INTO scratch "
            "(session_id, agent_id, key, value, created, updated, ttl_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, agent_id, key, value, created, now, ttl_s),
        )


def get(session_id: str, agent_id: str, key: str) -> Optional[str]:
    now = time.time()
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT value, updated, ttl_s FROM scratch "
            "WHERE session_id=? AND agent_id=? AND key=?",
            (session_id, agent_id, key),
        ).fetchone()
    if row is None:
        return None
    value, updated, ttl_s = row
    if ttl_s is not None and (now - updated) > ttl_s:
        return None   # expired
    return value


def get_all(session_id: str) -> list[ScratchRow]:
    now = time.time()
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT session_id, agent_id, key, value, created, updated, ttl_s "
            "FROM scratch WHERE session_id=? ORDER BY updated DESC",
            (session_id,),
        ).fetchall()
    out: list[ScratchRow] = []
    for r in rows:
        sid, aid, k, v, cr, up, ttl = r
        if ttl is not None and (now - up) > ttl:
            continue
        out.append(ScratchRow(sid, aid, k, v, cr, up, ttl))
    return out


def peek(session_id: str, agent_glob: str = "*", key_glob: str = "*") -> list[ScratchRow]:
    """Glob-filtered read. Uses SQLite GLOB operator (shell-style patterns)."""
    now = time.time()
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT session_id, agent_id, key, value, created, updated, ttl_s "
            "FROM scratch "
            "WHERE session_id=? AND agent_id GLOB ? AND key GLOB ? "
            "ORDER BY updated DESC",
            (session_id, agent_glob, key_glob),
        ).fetchall()
    out: list[ScratchRow] = []
    for r in rows:
        sid, aid, k, v, cr, up, ttl = r
        if ttl is not None and (now - up) > ttl:
            continue
        out.append(ScratchRow(sid, aid, k, v, cr, up, ttl))
    return out


def delete(session_id: str, agent_id: Optional[str] = None,
           key: Optional[str] = None) -> int:
    clauses  = ["session_id=?"]
    params:  list = [session_id]
    if agent_id is not None:
        clauses.append("agent_id=?");  params.append(agent_id)
    if key is not None:
        clauses.append("key=?");       params.append(key)
    with _lock, _conn() as c:
        cur = c.execute(
            f"DELETE FROM scratch WHERE {' AND '.join(clauses)}",
            params,
        )
        return cur.rowcount


def cleanup_expired(now: Optional[float] = None) -> int:
    """Delete rows past their TTL. Returns count removed. Safe to call
    periodically (fast when no expired rows)."""
    now = now if now is not None else time.time()
    with _lock, _conn() as c:
        cur = c.execute(
            "DELETE FROM scratch WHERE ttl_s IS NOT NULL AND (? - updated) > ttl_s",
            (now,),
        )
        return cur.rowcount


__all__ = [
    "put", "get", "get_all", "peek", "delete", "cleanup_expired",
    "ScratchRow",
]
