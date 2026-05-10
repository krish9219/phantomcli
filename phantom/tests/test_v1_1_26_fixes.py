"""Tests for v1.1.26 — read-allowlist expansion, edit_file diff,
tool icons + result preview, doctor --chat smoke test, markdown rendering."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from phantom.agent.tools import default_tools
from phantom.cli.chat import (
    _format_diff,
    _format_tool_call,
    _format_tool_result_preview,
    _render_assistant_reply,
    _tool_icon,
    _TOOL_ICONS,
)
from phantom.tools.fs import edit_file


# ─── Read allowlist now includes ~/.phantom/ ────────────────────────────────

def test_read_allowlist_includes_phantom_home(tmp_path: Path, monkeypatch):
    """default_tools should give read_file access to ~/.phantom/* even
    when the workspace is set elsewhere. Fixes the v2-prompt-#8 case
    where reading profile.json from the home dir was blocked."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    phantom_home = tmp_path / "phantom_home"
    phantom_home.mkdir()
    monkeypatch.setenv("PHANTOM_HOME", str(phantom_home))

    # Drop a profile.json in the simulated phantom home.
    profile_path = phantom_home / "profile.json"
    profile_path.write_text('{"user_name": "Aravind", "workspace_path": "/tmp"}')

    tools = default_tools(workdir=str(workspace))
    read_tool = next(t for t in tools if t.name == "read_file")
    result = json.loads(read_tool.handler({"path": str(profile_path)}))
    assert result["ok"] is True
    assert "Aravind" in result["text"]


def test_write_allowlist_does_not_include_phantom_home(tmp_path: Path, monkeypatch):
    """Writes to ~/.phantom/ must STILL be blocked — only reads are
    expanded. Otherwise the agent could overwrite the user's profile."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    phantom_home = tmp_path / "phantom_home"
    phantom_home.mkdir()
    monkeypatch.setenv("PHANTOM_HOME", str(phantom_home))

    tools = default_tools(workdir=str(workspace))
    write_tool = next(t for t in tools if t.name == "write_file")
    result = json.loads(write_tool.handler({
        "path": str(phantom_home / "newfile.txt"),
        "text": "hello",
    }))
    assert result["ok"] is False
    assert "allowlist" in result["error"].lower()


# ─── edit_file returns a unified diff ──────────────────────────────────────

def test_edit_file_returns_diff(tmp_path: Path):
    target = tmp_path / "x.py"
    target.write_text("def add(a, b):\n    return a - b\n")
    result = edit_file(
        path=str(target),
        old_string="return a - b",
        new_string="return a + b",
        allowlist=(str(tmp_path),),
    )
    assert result["ok"] is True
    assert "diff" in result
    diff = result["diff"]
    assert "-    return a - b" in diff
    assert "+    return a + b" in diff


def test_edit_file_diff_is_truncated_for_huge_edits(tmp_path: Path):
    """A 1000-line replacement shouldn't flood the diff field."""
    big_before = "\n".join(f"line {i} OLD" for i in range(200))
    big_after = "\n".join(f"line {i} NEW" for i in range(200))
    target = tmp_path / "big.txt"
    target.write_text(big_before)
    result = edit_file(
        path=str(target),
        old_string=big_before, new_string=big_after,
        allowlist=(str(tmp_path),),
    )
    assert result["ok"] is True
    assert "more diff lines" in result["diff"] or len(result["diff"].splitlines()) <= 50


# ─── Tool icons map ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("run_bash", "⚡"),
    ("write_file", "📝"),
    ("edit_file", "✏️ "),
    ("read_file", "🔍"),
    ("list_dir", "📂"),
    ("web_search", "🌐"),
    ("memory_add", "💾"),
    ("start_server", "🚀"),
])
def test_tool_icon_returns_emoji(name, expected):
    assert _tool_icon(name) == expected


def test_tool_icon_unknown_falls_back_to_arrow():
    assert _tool_icon("not_a_real_tool") == "→"


def test_all_default_tools_have_icons(tmp_path: Path):
    """Every tool registered in default_tools has an icon. Catches the
    case where a new tool ships without UX metadata."""
    tools = default_tools(workdir=str(tmp_path))
    for t in tools:
        assert t.name in _TOOL_ICONS, f"tool {t.name!r} has no icon registered"


# ─── _format_tool_result_preview ────────────────────────────────────────────

def test_preview_run_bash_success():
    result = json.dumps({"exit_code": 0, "stdout": "Hello, World!\nbye\n"})
    out = _format_tool_result_preview("run_bash", result)
    assert "Hello, World!" in out
    assert "✓" in out


def test_preview_run_bash_failure():
    result = json.dumps({"exit_code": 1, "stdout": "", "stderr": "error"})
    out = _format_tool_result_preview("run_bash", result)
    assert "exit 1" in out


def test_preview_write_file_shows_byte_count():
    result = json.dumps({"ok": True, "bytes_written": 1234})
    out = _format_tool_result_preview("write_file", result)
    assert "1234" in out


def test_preview_edit_file_renders_diff():
    """edit_file results with a diff field render in red/green."""
    result = json.dumps({
        "ok": True, "replacements": 1,
        "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new",
    })
    out = _format_tool_result_preview("edit_file", result)
    # Contains both the - line and the + line.
    assert "-old" in out
    assert "+new" in out


def test_preview_start_server_shows_url():
    result = json.dumps({
        "url": "http://127.0.0.1:5000",
        "listening": True,
        "pid": 12345,
    })
    out = _format_tool_result_preview("start_server", result)
    assert "127.0.0.1:5000" in out
    assert "✓" in out


def test_preview_error_shows_red_x():
    result = json.dumps({"error": "file not found"})
    out = _format_tool_result_preview("read_file", result)
    assert "×" in out
    assert "file not found" in out


def test_preview_handles_invalid_json_gracefully():
    out = _format_tool_result_preview("run_bash", "not-json")
    assert out == ""


# ─── _format_diff colours ──────────────────────────────────────────────────

def test_format_diff_colours_added_and_removed_lines():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-return a - b\n+return a + b"
    out = _format_diff(diff)
    # Red for "-", green for "+", cyan for "@@".
    assert "\033[31m" in out  # red
    assert "\033[32m" in out  # green
    assert "\033[36m" in out  # cyan
    assert "return a - b" in out
    assert "return a + b" in out


# ─── Markdown rendering doesn't crash on edge cases ────────────────────────

def test_render_assistant_reply_handles_empty_string(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    _render_assistant_reply("")
    captured = capsys.readouterr()
    # Just a newline, no exception.
    assert captured.out == "\n"


def test_render_assistant_reply_falls_back_to_plain_text_on_non_tty(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    _render_assistant_reply("# Hello\n\n- item 1\n- item 2")
    captured = capsys.readouterr()
    # On non-TTY we don't try to render markdown — just print as-is.
    assert "# Hello" in captured.out
    assert "- item 1" in captured.out


# ─── doctor --chat smoke test ──────────────────────────────────────────────

def test_chat_smoke_test_passes_on_healthy_system():
    """The smoke test should pass — every import + PromptSession build +
    boot banner render works cleanly.

    Windows CI runners are headless and don't expose a real console
    screen buffer; prompt_toolkit raises ``NoConsoleScreenBufferError``
    when it tries to attach. The production behaviour on a real Windows
    user terminal is correct — the runner is just headless. Skip there
    rather than skip-everywhere with a less specific guard."""
    import sys
    if sys.platform == "win32" and not sys.stdout.isatty():
        import pytest
        pytest.skip(
            "Windows CI runner has no console screen buffer for "
            "prompt_toolkit; production Windows terminals work fine."
        )
    from phantom.cli import _chat_smoke_test
    rc = _chat_smoke_test()
    assert rc == 0
