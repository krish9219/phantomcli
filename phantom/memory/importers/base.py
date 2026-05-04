"""Importer base class and shared records.

The contract is intentionally tiny. Each importer:

1. Is constructed with a path (or list of paths) to scan.
2. Returns ``ImportedSession`` objects from :meth:`sessions`.
3. Each session yields ``ImportedTurn`` objects from :attr:`turns`.

The orchestrator handles namespace assignment, dedupe, and writing to
:class:`phantom.memory.MemoryStore`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

__all__ = ["ImportedSession", "ImportedTurn", "Importer"]


@dataclass(frozen=True, slots=True)
class ImportedTurn:
    role: str   # "user" | "assistant" | "system"
    text: str
    timestamp_iso: str = ""
    tool_calls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportedSession:
    source: str          # importer name, e.g. "claude-code"
    session_id: str      # importer-specific id (file path, uuid, etc.)
    started_at_iso: str
    project_path: str = ""
    turns: tuple[ImportedTurn, ...] = field(default_factory=tuple)


class Importer(ABC):
    """Abstract importer."""

    name: str = ""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else self.default_root()

    @abstractmethod
    def default_root(self) -> Path:
        """Return the OS-level default location for this harness."""

    @abstractmethod
    def sessions(self) -> Iterator[ImportedSession]:
        """Yield every session found beneath ``root``."""

    def __iter__(self) -> Iterator[ImportedSession]:
        return self.sessions()

    def collect(self) -> list[ImportedSession]:
        return list(self.sessions())
