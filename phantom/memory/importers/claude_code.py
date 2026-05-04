"""Claude Code transcript importer.

Claude Code stores conversations as JSONL under
``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``. Each line is one
event. We care about ``user`` and ``assistant`` events; everything else
is dropped.

The format has shifted across Claude Code versions; this importer is
liberal in what it accepts:

* either ``message.content`` (string) or ``message.content[].text``
  (Anthropic-style content blocks).
* either top-level ``timestamp`` or nested ``message.timestamp``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

from phantom.memory.importers.base import ImportedSession, ImportedTurn, Importer

__all__ = ["ClaudeCodeImporter"]


class ClaudeCodeImporter(Importer):
    name = "claude-code"

    def default_root(self) -> Path:
        return Path(os.path.expanduser("~/.claude/projects"))

    def sessions(self) -> Iterator[ImportedSession]:
        if not self.root.exists():
            return
        for project_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for transcript in sorted(project_dir.glob("*.jsonl")):
                yield self._parse_one(transcript, project_dir.name)

    def _parse_one(self, path: Path, project_dir_name: str) -> ImportedSession:
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
                    role = self._role_of(ev)
                    if role not in ("user", "assistant"):
                        continue
                    text = self._text_of(ev)
                    if not text:
                        continue
                    ts = ev.get("timestamp") or (ev.get("message") or {}).get("timestamp") or ""
                    if not started_at:
                        started_at = str(ts)
                    turns.append(
                        ImportedTurn(
                            role=role,
                            text=text,
                            timestamp_iso=str(ts),
                            tool_calls=self._tool_calls_of(ev),
                        )
                    )
        except OSError:
            pass
        return ImportedSession(
            source=self.name,
            session_id=path.stem,
            started_at_iso=started_at,
            project_path=project_dir_name.replace("-", "/"),
            turns=tuple(turns),
        )

    @staticmethod
    def _role_of(ev: dict[str, Any]) -> str:
        # Newer Claude Code: top-level "type" = "user" | "assistant".
        t = ev.get("type")
        if t in ("user", "assistant"):
            return t
        # Older: nested message.role
        msg = ev.get("message") or {}
        if isinstance(msg, dict):
            r = msg.get("role")
            if r in ("user", "assistant", "system"):
                return r
        return ""

    @staticmethod
    def _text_of(ev: dict[str, Any]) -> str:
        msg = ev.get("message") or {}
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                    elif isinstance(block, str):
                        parts.append(block)
                if parts:
                    return "\n".join(parts).strip()
        # very old format: top-level "text"
        if isinstance(ev.get("text"), str):
            return ev["text"].strip()
        return ""

    @staticmethod
    def _tool_calls_of(ev: dict[str, Any]) -> tuple[str, ...]:
        msg = ev.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            return ()
        names = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                n = block.get("name")
                if isinstance(n, str):
                    names.append(n)
        return tuple(names)
