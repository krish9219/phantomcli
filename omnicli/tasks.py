"""Live task tracker for PhantomCLI.

Provides a small in-memory task list so the user can see progress
(pending / running / done / failed) while a prompt is being processed.

Wiring points:
  - engine.generate_response(..., on_task=callback) receives updates.
  - cli.py renders a Rich Live panel.
  - dashboard.py forwards updates as WebSocket `task_update` events.
  - telegram_bot.py can emit edit_message updates on the same callback.

Design:
  The tracker is intentionally minimal: a list of Task dataclasses with
  an optional on_change callback fired on every mutation. All access is
  guarded by a threading lock so it's safe to call from tool-execution
  threads spawned by the agent orchestrator.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional


TaskStatus = str  # "pending" | "running" | "done" | "failed"


_STATUS_ICON = {
    "pending": "◻",
    "running": "◼",
    "done": "✔",
    "failed": "✖",
}


@dataclass
class Task:
    id: str
    name: str
    status: TaskStatus = "pending"
    detail: str = ""
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    def duration(self) -> Optional[float]:
        if self.start_time is None:
            return None
        end = self.end_time if self.end_time is not None else time.monotonic()
        return max(0.0, end - self.start_time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["icon"] = _STATUS_ICON.get(self.status, "?")
        dur = self.duration()
        d["duration"] = round(dur, 2) if dur is not None else None
        return d


class TaskTracker:
    """Thread-safe task list with change notifications."""

    def __init__(self, on_change: Optional[Callable[["TaskTracker"], None]] = None):
        self._tasks: list[Task] = []
        self._lock = threading.RLock()
        self._on_change = on_change

    def set_callback(self, on_change: Optional[Callable[["TaskTracker"], None]]) -> None:
        with self._lock:
            self._on_change = on_change

    def _fire(self) -> None:
        cb = self._on_change
        if cb is None:
            return
        try:
            cb(self)
        except Exception:
            # Never let UI callbacks break the engine.
            pass

    def add(self, name: str, detail: str = "", status: TaskStatus = "pending") -> str:
        task_id = uuid.uuid4().hex[:8]
        with self._lock:
            t = Task(id=task_id, name=name, detail=detail, status=status)
            if status == "running":
                t.start_time = time.monotonic()
            self._tasks.append(t)
        self._fire()
        return task_id

    def start(self, task_id: str, detail: Optional[str] = None) -> None:
        with self._lock:
            t = self._find(task_id)
            if t is None:
                return
            t.status = "running"
            if t.start_time is None:
                t.start_time = time.monotonic()
            if detail is not None:
                t.detail = detail
        self._fire()

    def finish(self, task_id: str, ok: bool = True, detail: Optional[str] = None) -> None:
        with self._lock:
            t = self._find(task_id)
            if t is None:
                return
            t.status = "done" if ok else "failed"
            t.end_time = time.monotonic()
            if t.start_time is None:
                t.start_time = t.end_time
            if detail is not None:
                t.detail = detail
        self._fire()

    def update_detail(self, task_id: str, detail: str) -> None:
        with self._lock:
            t = self._find(task_id)
            if t is None:
                return
            t.detail = detail
        self._fire()

    def plan(self, names: list[str]) -> list[str]:
        """Seed a batch of pending tasks in one shot (used by plan_tasks tool)."""
        ids: list[str] = []
        with self._lock:
            for name in names:
                task_id = uuid.uuid4().hex[:8]
                self._tasks.append(Task(id=task_id, name=name))
                ids.append(task_id)
        self._fire()
        return ids

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
        self._fire()

    def snapshot(self) -> list[Task]:
        with self._lock:
            return [Task(**asdict(t)) for t in self._tasks]

    def to_dicts(self) -> list[dict]:
        with self._lock:
            return [t.to_dict() for t in self._tasks]

    def summary(self) -> dict:
        with self._lock:
            counts = {"pending": 0, "running": 0, "done": 0, "failed": 0}
            for t in self._tasks:
                counts[t.status] = counts.get(t.status, 0) + 1
            return {
                "total": len(self._tasks),
                **counts,
            }

    def render(self, rich: bool = False) -> str:
        """Text rendering for terminal/Telegram. With rich=True, applies
        Rich markup so completed tasks render with strikethrough and a
        progress header (`X/Y done`) is prepended."""
        with self._lock:
            if not self._tasks:
                return ""
            done = sum(1 for t in self._tasks if t.status == "done")
            total = len(self._tasks)
            lines: list[str] = []
            if rich:
                lines.append(f"[bold cyan]Plan:[/bold cyan] [dim]{done}/{total} done[/dim]")
            for t in self._tasks:
                icon = _STATUS_ICON.get(t.status, "?")
                name = t.name
                dur = t.duration()
                suffix = ""
                if t.status in ("done", "failed") and dur is not None:
                    suffix = f" ({dur:.1f}s)"
                if t.detail and not rich:
                    suffix += f" — {t.detail[:80]}"
                if rich:
                    if t.status == "done":
                        lines.append(f"  [green]{icon}[/green] [strike dim]{name}[/strike dim][dim]{suffix}[/dim]")
                    elif t.status == "running":
                        lines.append(f"  [yellow]{icon}[/yellow] [bold]{name}[/bold]{suffix}")
                    elif t.status == "failed":
                        detail = f" [dim]— {t.detail[:60]}[/dim]" if t.detail else ""
                        lines.append(f"  [red]{icon}[/red] [strike]{name}[/strike]{suffix}{detail}")
                    else:
                        lines.append(f"  [dim]{icon} {name}{suffix}[/dim]")
                else:
                    lines.append(f"{icon} {name}{suffix}")
            return "\n".join(lines)

    def _find(self, task_id: str) -> Optional[Task]:
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None


def status_icon(status: TaskStatus) -> str:
    return _STATUS_ICON.get(status, "?")
