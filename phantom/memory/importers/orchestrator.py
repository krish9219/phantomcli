"""Run an importer and write into the memory store."""

from __future__ import annotations

from dataclasses import dataclass

from phantom.memory.importers.base import Importer

__all__ = ["ImportSummary", "import_to_memory"]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    source: str
    sessions: int
    turns: int
    written: int
    skipped: int


def import_to_memory(
    importer: Importer,
    *,
    store=None,
    namespace: str = "imported",
    dry_run: bool = False,
) -> ImportSummary:
    """Walk the importer's sessions and persist them to ``store``.

    ``store`` is a :class:`phantom.memory.MemoryStore` (or anything with
    a compatible ``write`` method). Pass ``None`` for a dry-run that
    only counts.
    """
    sessions = 0
    turns = 0
    written = 0
    skipped = 0
    for session in importer:
        sessions += 1
        for turn in session.turns:
            turns += 1
            if dry_run or store is None:
                skipped += 1
                continue
            try:
                # Try the v2 MemoryStore.write signature; fall back to add().
                if hasattr(store, "write"):
                    store.write(
                        namespace=f"{namespace}/{session.source}/{session.session_id}",
                        text=f"[{turn.role}] {turn.text}",
                        metadata={
                            "source": session.source,
                            "role": turn.role,
                            "timestamp": turn.timestamp_iso,
                        },
                    )
                else:
                    store.add(text=turn.text, role=turn.role)
                written += 1
            except Exception:
                skipped += 1
    return ImportSummary(
        source=importer.name,
        sessions=sessions,
        turns=turns,
        written=written,
        skipped=skipped,
    )
