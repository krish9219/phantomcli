"""Cross-harness session importers.

Read transcripts from competing CLI agents and write them to Phantom's
episodic memory. The dream: a user can ``phantom memory import claude-code``
once and continue every Claude Code conversation in Phantom with full
context preserved.

Each importer reads the harness's on-disk transcript format and yields
:class:`ImportedTurn` records. The orchestrator
(:func:`import_to_memory`) writes them into a :class:`MemoryStore`.
"""

from __future__ import annotations

from phantom.memory.importers.base import (
    ImportedSession,
    ImportedTurn,
    Importer,
)
from phantom.memory.importers.claude_code import ClaudeCodeImporter
from phantom.memory.importers.codex import CodexImporter
from phantom.memory.importers.opencode import OpenCodeImporter

__all__ = [
    "ClaudeCodeImporter",
    "CodexImporter",
    "ImportedSession",
    "ImportedTurn",
    "Importer",
    "OpenCodeImporter",
    "all_importers",
]


def all_importers() -> dict[str, type[Importer]]:
    """Return the public {name: class} importer registry."""
    return {
        "claude-code": ClaudeCodeImporter,
        "codex": CodexImporter,
        "opencode": OpenCodeImporter,
    }
