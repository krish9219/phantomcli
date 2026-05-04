"""
Live-reload watcher for CLAUDE.md / CONTEXT.md files.

Long-running Phantom sessions (REPL, web dashboard, telegram bot) need
to pick up CONTEXT.md edits without a restart. This module provides a
poll-based watcher (no inotify dep — portable, works on Windows too)
that compares file mtimes on a fixed interval and fires a callback
whenever any discovered CONTEXT.md file changes.

Design:
  * Background daemon thread. `start()` returns immediately.
  * Default poll interval 1.5 seconds.
  * Rediscovers files on every poll — if the user `git pull`s and
    gets a new CONTEXT.md, we'll start tracking it on the next tick.
  * `stop()` joins cleanly.
  * Callback signature: `on_change(paths_changed: list[str])`.
  * Test hook: `Watcher.poll_once()` does one iteration synchronously.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("omnicli.context_memory_watcher")


@dataclass
class Watcher:
    on_change:     Callable[[list[str]], None]
    project_dir:   Optional[str] = None
    interval_s:    float = 1.5
    _stop:         threading.Event = field(default_factory=threading.Event)
    _thread:       Optional[threading.Thread] = None
    _mtimes:       dict[str, float] = field(default_factory=dict)
    _initialized:  bool = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                         name="phantom-ctx-watcher")
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:
                log.warning("context_memory_watcher poll error: %s", e)
            # Use Event.wait so stop() can interrupt the sleep
            self._stop.wait(timeout=self.interval_s)

    def _current_files(self) -> list[str]:
        try:
            from omnicli.context_memory import discover
            return [f.path for f in discover(start=self.project_dir)]
        except Exception as e:
            log.debug("discover failed: %s", e)
            return []

    def poll_once(self) -> list[str]:
        """One poll iteration. Returns list of paths whose mtime changed
        (or that newly appeared) since the previous call. On the first
        call, seeds state without firing `on_change`."""
        current = self._current_files()
        now_mtimes: dict[str, float] = {}
        for p in current:
            try:
                now_mtimes[p] = os.path.getmtime(p)
            except OSError:
                continue

        changed: list[str] = []
        if not self._initialized:
            self._mtimes = now_mtimes
            self._initialized = True
            return []

        # New file or updated mtime
        for p, mt in now_mtimes.items():
            if p not in self._mtimes or self._mtimes[p] != mt:
                changed.append(p)

        # File removed counts too
        for p in self._mtimes.keys():
            if p not in now_mtimes:
                changed.append(p)

        if changed:
            try:
                self.on_change(changed)
            except Exception as e:
                log.warning("on_change callback raised: %s", e)

        self._mtimes = now_mtimes
        return changed


def start(
    on_change:   Callable[[list[str]], None],
    project_dir: Optional[str] = None,
    interval_s:  float = 1.5,
) -> Watcher:
    """Convenience: build + start a watcher, return the handle."""
    w = Watcher(on_change=on_change, project_dir=project_dir, interval_s=interval_s)
    w.start()
    return w


__all__ = ["Watcher", "start"]
