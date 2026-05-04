"""API-key pool with cooldown rotation."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from phantom.errors import PhantomError

__all__ = ["KeyEntry", "KeyPool", "KeyPoolEmptyError"]


class KeyPoolEmptyError(PhantomError):
    """Raised when all keys are in cooldown."""

    code = "phantom.auth.pool_empty"


@dataclass
class KeyEntry:
    """One key in the pool."""

    key: str
    cooldown_until: float = 0.0  # epoch seconds; 0 = available
    failures: int = 0
    last_used: float = 0.0


@dataclass
class KeyPool:
    """Thread-safe round-robin pool of API keys.

    Operations are O(N) in the size of the pool; for the typical N <= 4
    that's fine.
    """

    name: str
    _entries: list[KeyEntry] = field(default_factory=list)
    _index: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_keys(cls, name: str, keys: list[str]) -> "KeyPool":
        if len(keys) > 64:
            raise PhantomError("KeyPool capped at 64 keys")
        return cls(
            name=name,
            _entries=[KeyEntry(key=k) for k in keys],
        )

    def __len__(self) -> int:
        return len(self._entries)

    # ─── checkout / return ─────────────────────────────────────────────

    def checkout(self, *, now: float | None = None) -> KeyEntry:
        """Return the next available key. Raises KeyPoolEmptyError if all
        keys are cooling.
        """
        now = now if now is not None else time.time()
        with self._lock:
            if not self._entries:
                raise KeyPoolEmptyError(f"key pool {self.name!r} is empty")
            n = len(self._entries)
            for offset in range(n):
                idx = (self._index + offset) % n
                entry = self._entries[idx]
                if entry.cooldown_until <= now:
                    entry.last_used = now
                    self._index = (idx + 1) % n
                    return entry
            raise KeyPoolEmptyError(
                f"all keys in pool {self.name!r} are in cooldown"
            )

    def mark_failure(
        self,
        key: str,
        *,
        cooldown_s: float = 60.0,
        now: float | None = None,
    ) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            for entry in self._entries:
                if entry.key == key:
                    entry.failures += 1
                    entry.cooldown_until = now + cooldown_s
                    return

    def mark_success(self, key: str) -> None:
        with self._lock:
            for entry in self._entries:
                if entry.key == key:
                    entry.failures = 0
                    return

    def stats(self) -> list[dict]:
        """Snapshot for the dashboard."""
        with self._lock:
            return [
                {
                    "key_suffix": e.key[-4:] if len(e.key) >= 4 else "",
                    "failures": e.failures,
                    "cooling": e.cooldown_until > time.time(),
                    "last_used": e.last_used,
                }
                for e in self._entries
            ]
