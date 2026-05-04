"""OpenAI Codex CLI transcript importer.

Codex stores rollouts under ``~/.codex/sessions/<date>/<uuid>.jsonl``.
Each line is an event with ``type`` and a payload. We extract the
``message`` events with role ``user`` or ``assistant``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

from phantom.memory.importers.base import ImportedSession, ImportedTurn, Importer

__all__ = ["CodexImporter"]


class CodexImporter(Importer):
    name = "codex"

    def default_root(self) -> Path:
        return Path(os.path.expanduser("~/.codex/sessions"))

    def sessions(self) -> Iterator[ImportedSession]:
        if not self.root.exists():
            return
        for transcript in sorted(self.root.rglob("*.jsonl")):
            yield self._parse_one(transcript)

    def _parse_one(self, path: Path) -> ImportedSession:
        turns: list[ImportedTurn] = []
        started_at = ""
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") not in ("message", "user_message", "assistant_message"):
                        continue
                    role = ev.get("role") or ev.get("type", "").replace("_message", "")
                    if role not in ("user", "assistant"):
                        continue
                    text = self._text_of(ev)
                    if not text:
                        continue
                    ts = str(ev.get("timestamp") or ev.get("created_at") or "")
                    if not started_at:
                        started_at = ts
                    turns.append(ImportedTurn(role=role, text=text, timestamp_iso=ts))
        except OSError:
            pass
        return ImportedSession(
            source=self.name,
            session_id=path.stem,
            started_at_iso=started_at,
            project_path=str(path.parent),
            turns=tuple(turns),
        )

    @staticmethod
    def _text_of(ev: dict[str, Any]) -> str:
        for key in ("text", "content", "body"):
            v = ev.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, list):
                parts = [
                    str(b.get("text", b.get("content", "")))
                    for b in v
                    if isinstance(b, dict)
                ]
                joined = "\n".join(p for p in parts if p).strip()
                if joined:
                    return joined
        return ""
