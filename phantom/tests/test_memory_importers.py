"""Tests for cross-harness memory importers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.memory.importers import (
    ClaudeCodeImporter,
    CodexImporter,
    OpenCodeImporter,
    all_importers,
)
from phantom.memory.importers.orchestrator import ImportSummary, import_to_memory


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def claude_root(tmp_path: Path) -> Path:
    root = tmp_path / "claude"
    proj = root / "-Users-aravind-projects-foo"
    proj.mkdir(parents=True)
    transcript = proj / "abc123.jsonl"
    events = [
        {"type": "user", "message": {"role": "user", "content": "hello phantom"}, "timestamp": "2026-05-01T10:00:00Z"},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi back"}]}, "timestamp": "2026-05-01T10:00:02Z"},
        {"type": "tool_result", "message": {"role": "tool", "content": "ignored"}},
        {"type": "user", "message": {"role": "user", "content": "do a thing"}},
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events))
    return root


@pytest.fixture
def codex_root(tmp_path: Path) -> Path:
    root = tmp_path / "codex"
    day = root / "2026-05-01"
    day.mkdir(parents=True)
    f = day / "session-xyz.jsonl"
    events = [
        {"type": "user_message", "role": "user", "text": "hi", "timestamp": "1715000000"},
        {"type": "assistant_message", "role": "assistant", "text": "hello", "timestamp": "1715000001"},
        {"type": "tool_call", "name": "shell", "args": {}},
    ]
    f.write_text("\n".join(json.dumps(e) for e in events))
    return root


@pytest.fixture
def opencode_root(tmp_path: Path) -> Path:
    root = tmp_path / "opencode"
    sess = root / "session-001"
    sess.mkdir(parents=True)
    msgs = sess / "messages.json"
    msgs.write_text(json.dumps([
        {"role": "user", "content": "ok", "timestamp": "2026-05-02T00:00:00Z"},
        {"role": "assistant", "content": [{"text": "done"}], "timestamp": "2026-05-02T00:00:01Z"},
    ]))
    return root


# ─── claude-code ─────────────────────────────────────────────────────────────


def test_claude_importer_finds_session(claude_root: Path):
    sessions = ClaudeCodeImporter(root=claude_root).collect()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "claude-code"
    assert s.session_id == "abc123"
    assert len(s.turns) == 3
    assert s.turns[0].role == "user"
    assert s.turns[0].text == "hello phantom"
    assert s.turns[1].role == "assistant"
    assert s.turns[1].text == "hi back"


def test_claude_importer_handles_missing_root(tmp_path: Path):
    sessions = ClaudeCodeImporter(root=tmp_path / "nope").collect()
    assert sessions == []


def test_claude_importer_skips_garbage_lines(tmp_path: Path):
    root = tmp_path / "claude"
    proj = root / "x"
    proj.mkdir(parents=True)
    (proj / "x.jsonl").write_text("not json\n" + json.dumps({"type": "user", "message": {"role": "user", "content": "ok"}}))
    s = ClaudeCodeImporter(root=root).collect()
    assert len(s) == 1
    assert len(s[0].turns) == 1


# ─── codex ───────────────────────────────────────────────────────────────────


def test_codex_importer_finds_session(codex_root: Path):
    sessions = CodexImporter(root=codex_root).collect()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "codex"
    assert len(s.turns) == 2


def test_codex_importer_missing_root(tmp_path: Path):
    sessions = CodexImporter(root=tmp_path / "nope").collect()
    assert sessions == []


# ─── opencode ────────────────────────────────────────────────────────────────


def test_opencode_importer_finds_session(opencode_root: Path):
    sessions = OpenCodeImporter(root=opencode_root).collect()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "opencode"
    assert len(s.turns) == 2
    assert s.turns[1].text == "done"


# ─── registry / orchestrator ─────────────────────────────────────────────────


def test_registry_lists_three_importers():
    reg = all_importers()
    assert set(reg) == {"claude-code", "codex", "opencode"}


def test_orchestrator_dry_run_counts(claude_root: Path):
    importer = ClaudeCodeImporter(root=claude_root)
    summary = import_to_memory(importer, store=None, dry_run=True)
    assert isinstance(summary, ImportSummary)
    assert summary.sessions == 1
    assert summary.turns == 3
    assert summary.written == 0


class _FakeStore:
    def __init__(self):
        self.records = []

    def write(self, *, namespace, text, metadata):
        self.records.append((namespace, text, metadata))


def test_orchestrator_writes_to_store(claude_root: Path):
    importer = ClaudeCodeImporter(root=claude_root)
    store = _FakeStore()
    summary = import_to_memory(importer, store=store)
    assert summary.written == 3
    assert len(store.records) == 3
    assert all(ns.startswith("imported/claude-code/") for ns, _, _ in store.records)
