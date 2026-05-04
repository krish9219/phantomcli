"""Todo plugin — per-session task list, SQLite-backed.

Capability: ``memory``. The plugin writes to a SQLite file under its
own workdir (provided by :class:`PluginContext`); operators do not
need to grant filesystem capability.

Payload schema::

    {"action": "add" | "list" | "done" | "remove", ...}

* add:     {"action": "add",    "text": str}                  → {"id": int}
* list:    {"action": "list",   "include_done": bool=False}   → {"items": [...]}
* done:    {"action": "done",   "id": int}                    → {"updated": int}
* remove:  {"action": "remove", "id": int}                    → {"removed": int}
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from phantom.errors import PluginError
from phantom.plugins.capability import Capability
from phantom.plugins.plugin import Plugin, PluginContext

__all__ = ["TodoPlugin"]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL DEFAULT (julianday('now'))
);
"""


class TodoPlugin(Plugin):
    def _db_path(self, ctx: PluginContext) -> Path:
        return Path(ctx.workdir) / "todo.sqlite"

    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        if Capability.MEMORY not in ctx.capabilities:
            raise PluginError("todo plugin requires the 'memory' capability")

        action = payload.get("action")
        path = self._db_path(ctx)

        with closing(sqlite3.connect(path)) as con:
            con.row_factory = sqlite3.Row
            con.executescript(_SCHEMA)

            if action == "add":
                text = payload.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    raise PluginError("todo add: 'text' must be a non-empty string")
                cur = con.execute("INSERT INTO todos(text) VALUES (?)", (text.strip(),))
                con.commit()
                return {"id": cur.lastrowid}

            if action == "list":
                include_done = bool(payload.get("include_done", False))
                rows = con.execute(
                    "SELECT id, text, done FROM todos"
                    + ("" if include_done else " WHERE done = 0")
                    + " ORDER BY id"
                ).fetchall()
                return {
                    "items": [
                        {"id": r["id"], "text": r["text"], "done": bool(r["done"])}
                        for r in rows
                    ]
                }

            if action == "done":
                tid = payload.get("id")
                if not isinstance(tid, int):
                    raise PluginError("todo done: 'id' must be an integer")
                cur = con.execute("UPDATE todos SET done = 1 WHERE id = ?", (tid,))
                con.commit()
                return {"updated": cur.rowcount}

            if action == "remove":
                tid = payload.get("id")
                if not isinstance(tid, int):
                    raise PluginError("todo remove: 'id' must be an integer")
                cur = con.execute("DELETE FROM todos WHERE id = ?", (tid,))
                con.commit()
                return {"removed": cur.rowcount}

            raise PluginError(
                f"todo: unknown action {action!r} "
                f"(allowed: 'add', 'list', 'done', 'remove')"
            )
