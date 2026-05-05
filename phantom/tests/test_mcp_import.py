"""Tests for MCP config auto-import."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from phantom.mcp.import_config import (
    discover_sources,
    import_mcp_configs,
    phantom_mcp_path,
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    return tmp_path


def _write_mcp(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}))


def test_discover_finds_user_configs(fake_home: Path):
    _write_mcp(fake_home / ".claude" / "mcp.json", {"weather": {"command": "/usr/bin/weather"}})
    _write_mcp(fake_home / ".codex" / "mcp.json", {"github": {"command": "/usr/bin/github"}})
    sources = discover_sources(cwd=fake_home)
    labels = [label for label, _ in sources]
    assert "claude-code (user)" in labels
    assert "codex (user)" in labels


def test_import_writes_target(fake_home: Path):
    _write_mcp(fake_home / ".claude" / "mcp.json", {
        "weather": {"command": "/usr/bin/weather", "args": ["--units", "metric"]}
    })
    summary = import_mcp_configs(cwd=fake_home)
    assert summary.servers_added == 1
    assert summary.added_names == ("weather",)
    target = phantom_mcp_path()
    assert target.exists()
    body = json.loads(target.read_text())
    assert body["mcpServers"]["weather"]["command"] == "/usr/bin/weather"
    assert body["mcpServers"]["weather"]["args"] == ["--units", "metric"]


def test_import_preserves_existing_entry(fake_home: Path):
    target = phantom_mcp_path()
    target.write_text(json.dumps({"mcpServers": {"weather": {"command": "/already-here"}}}))
    _write_mcp(fake_home / ".claude" / "mcp.json", {"weather": {"command": "/different"}})
    summary = import_mcp_configs(cwd=fake_home)
    assert summary.servers_added == 0
    assert summary.servers_skipped_existing == 1
    body = json.loads(target.read_text())
    assert body["mcpServers"]["weather"]["command"] == "/already-here"


def test_dry_run_writes_nothing(fake_home: Path):
    _write_mcp(fake_home / ".claude" / "mcp.json", {"x": {"command": "/x"}})
    summary = import_mcp_configs(cwd=fake_home, dry_run=True)
    assert summary.servers_added == 1  # would have added
    assert not phantom_mcp_path().exists()


def test_invalid_entries_are_skipped(fake_home: Path):
    _write_mcp(fake_home / ".claude" / "mcp.json", {
        "good": {"command": "/good"},
        "no-command": {"args": ["x"]},          # missing command, skipped
        "wrong-type": "string-not-dict",         # wrong type, skipped
    })
    summary = import_mcp_configs(cwd=fake_home)
    assert summary.servers_added == 1
    assert summary.added_names == ("good",)


def test_first_source_wins_on_collision(fake_home: Path):
    _write_mcp(fake_home / ".claude" / "mcp.json", {"shared": {"command": "/from-claude"}})
    _write_mcp(fake_home / ".codex" / "mcp.json", {"shared": {"command": "/from-codex"}})
    summary = import_mcp_configs(cwd=fake_home)
    assert summary.servers_added == 1
    body = json.loads(phantom_mcp_path().read_text())
    assert body["mcpServers"]["shared"]["command"] == "/from-claude"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file-mode bits aren't enforceable on Windows")
def test_target_file_perms_owner_only(fake_home: Path):
    _write_mcp(fake_home / ".claude" / "mcp.json", {"x": {"command": "/x"}})
    import_mcp_configs(cwd=fake_home)
    target = phantom_mcp_path()
    if hasattr(os, "stat"):
        mode = os.stat(target).st_mode & 0o777
        assert mode == 0o600
