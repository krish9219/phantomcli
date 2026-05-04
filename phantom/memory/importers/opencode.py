"""OpenCode transcript importer.

OpenCode stores conversations as JSON files under
``~/.local/share/opencode/sessions/<id>/messages.json`` (Linux). Each
file is a JSON array of message objects with ``role`` and ``content``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

from phantom.memory.importers.base import ImportedSession, ImportedTurn, Importer

__all__ = ["OpenCodeImporter"]


class OpenCodeImporter(Importer):
    name = "opencode"

    def default_root(self) -> Path:
        return Path(
            os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        ) / "opencode" / "sessions"

    def sessions(self) -> Iterator[ImportedSession]:
        if not self.root.exists():
            return
        for session_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            messages = session_dir / "messages.json"
            if messages.exists():
                yield self._parse_one(messages, session_dir.name)

    def _parse_one(self, path: Path, session_id: str) -> ImportedSession:
        turns: list[ImportedTurn] = []
        started_at = ""
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return ImportedSession(self.name, session_id, "")
        if not isinstance(data, list):
            return ImportedSession(self.name, session_id, "")
        for msg in data:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = self._text_of(msg)
            if not text:
                continue
            ts = str(msg.get("timestamp") or msg.get("created_at") or "")
            if not started_at:
                started_at = ts
            turns.append(ImportedTurn(role=role, text=text, timestamp_iso=ts))
        return ImportedSession(
            source=self.name,
            session_id=session_id,
            started_at_iso=started_at,
            project_path=str(path.parent),
            turns=tuple(turns),
        )

    @staticmethod
    def _text_of(msg: dict[str, Any]) -> str:
        c = msg.get("content")
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            parts = []
            for b in c:
                if isinstance(b, dict):
                    parts.append(str(b.get("text", b.get("content", ""))))
                elif isinstance(b, str):
                    parts.append(b)
            return "\n".join(p for p in parts if p).strip()
        return ""
