"""Progress tracker for long-running ops.

Wraps :class:`rich.progress.Progress` for the polished visual, but the
counting logic (steps_done, percentage, ETA) is pure-Python so tests
don't need a real terminal.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

__all__ = ["ProgressTracker", "ProgressSnapshot"]


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    description: str
    completed: int
    total: int
    elapsed_s: float
    eta_s: Optional[float]

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(1.0, self.completed / self.total)

    @property
    def percent(self) -> float:
        return round(self.fraction * 100, 1)


@dataclass
class ProgressTracker:
    description: str
    total: int
    completed: int = 0
    _started_at: float = field(default_factory=time.monotonic)
    _live: object = None  # rich.progress.Progress when in __enter__

    def advance(self, n: int = 1) -> None:
        if n < 0:
            raise ValueError("advance must be non-negative")
        self.completed = min(self.total, self.completed + n)
        if self._live is not None:
            try:
                self._live.update(self._task_id, advance=n)
            except Exception:
                pass

    def set_description(self, desc: str) -> None:
        self.description = desc
        if self._live is not None:
            try:
                self._live.update(self._task_id, description=desc)
            except Exception:
                pass

    def snapshot(self) -> ProgressSnapshot:
        elapsed = time.monotonic() - self._started_at
        eta: Optional[float]
        if self.completed > 0 and self.completed < self.total:
            rate = self.completed / elapsed if elapsed > 0 else 0
            eta = (self.total - self.completed) / rate if rate > 0 else None
        else:
            eta = None
        return ProgressSnapshot(
            description=self.description,
            completed=self.completed,
            total=self.total,
            elapsed_s=round(elapsed, 3),
            eta_s=round(eta, 3) if eta is not None else None,
        )

    # ── context manager ──────────────────────────────────────────────

    def __enter__(self):
        try:
            from rich.progress import (
                BarColumn, MofNCompleteColumn, Progress, TextColumn,
                TimeElapsedColumn, TimeRemainingColumn,
            )
        except ImportError:
            # Rich missing — silent no-op TUI; counting still works.
            return self
        self._live = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("ETA"),
            TimeRemainingColumn(),
        )
        self._live.start()
        self._task_id = self._live.add_task(self.description, total=self.total)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None
        return False


@contextmanager
def progress(description: str, total: int) -> Iterator[ProgressTracker]:
    tracker = ProgressTracker(description=description, total=total)
    with tracker:
        yield tracker
