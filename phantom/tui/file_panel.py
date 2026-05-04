"""File-update side panel — running list of files the agent has touched.

Pure data model; renders to a Rich panel on demand. Tests assert on the
data shape; the rendered Panel goes through ``console.export_text()``
when we need a string snapshot.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

__all__ = ["FileUpdate", "FileUpdateSidePanel"]


@dataclass(frozen=True, slots=True)
class FileUpdate:
    path: str
    action: str   # "created" | "modified" | "deleted" | "renamed"
    delta_added: int = 0
    delta_removed: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def short_path(self) -> str:
        # Keep the last 2 path components for display.
        parts = self.path.split("/")
        if len(parts) <= 2:
            return self.path
        return "…/" + "/".join(parts[-2:])


class FileUpdateSidePanel:
    """Bounded LRU of recent file edits."""

    def __init__(self, max_entries: int = 20) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self.max_entries = max_entries
        self._entries: OrderedDict[str, FileUpdate] = OrderedDict()

    # ── mutation ────────────────────────────────────────────────────

    def record(self, update: FileUpdate) -> None:
        # Move to end (most recent), bounded by max_entries
        if update.path in self._entries:
            del self._entries[update.path]
        self._entries[update.path] = update
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    # ── inspection ──────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries.values())

    def latest(self, n: int = 5) -> list[FileUpdate]:
        return list(self._entries.values())[-n:]

    def by_action(self, action: str) -> list[FileUpdate]:
        return [u for u in self._entries.values() if u.action == action]

    # ── rendering ───────────────────────────────────────────────────

    def render_text(self, *, max_rows: Optional[int] = None) -> str:
        """Plain-text rendering — no Rich required. For tests + pipes."""
        rows = list(self._entries.values())
        if max_rows is not None:
            rows = rows[-max_rows:]
        if not rows:
            return "(no file updates yet)"
        lines: list[str] = []
        action_glyph = {"created": "+", "modified": "~", "deleted": "-", "renamed": "→"}
        for u in rows:
            g = action_glyph.get(u.action, "?")
            delta = ""
            if u.delta_added or u.delta_removed:
                delta = f"  +{u.delta_added}/-{u.delta_removed}"
            lines.append(f"  {g} {u.short_path}{delta}")
        return "\n".join(lines)

    def render_panel(self, title: str = "files touched"):
        """Return a Rich Panel. Caller must have rich installed."""
        from rich.panel import Panel
        from rich.table import Table

        table = Table.grid(padding=(0, 1))
        table.add_column(justify="left")
        table.add_column(justify="left")
        table.add_column(justify="right", style="dim")
        action_glyph = {"created": "+", "modified": "~", "deleted": "-", "renamed": "→"}
        action_style = {"created": "green", "modified": "yellow", "deleted": "red", "renamed": "cyan"}
        for u in self._entries.values():
            g = action_glyph.get(u.action, "?")
            color = action_style.get(u.action, "white")
            delta = (f"+{u.delta_added}/-{u.delta_removed}"
                     if (u.delta_added or u.delta_removed) else "")
            table.add_row(f"[{color}]{g}[/{color}]", u.short_path, delta)
        return Panel(table, title=title, border_style="cyan")
