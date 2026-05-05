"""Regression net: every v1.0 module uses explicit UTF-8 for text I/O.

Why this matters
----------------

Python's default encoding for ``open()`` and ``Path.read_text()`` is
platform-dependent:

* Linux / macOS: UTF-8
* Windows: cp1252 (or whatever the system code page is set to)

A module that omits ``encoding="utf-8"`` works on POSIX and silently
mangles any non-ASCII content on Windows (configs, plugin manifests,
chat transcripts). This test scans our v1.0 modules and fails if any
text-mode open lacks an explicit encoding.

What's *not* checked here
-------------------------

* Pre-existing code in the legacy ``omnicli/`` package — that's frozen.
* Test files (their working dir is ours; encoding is fine).
* Binary-mode opens (``rb``, ``wb``) — encoding is irrelevant.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PHANTOM_DIR = Path(__file__).resolve().parent.parent

# Files we know must be UTF-8-safe end-to-end (the v1.0 surface).
V1_MODULES = [
    "daemon/transport.py",
    "daemon/protocol.py",
    "daemon/server.py",
    "daemon/client.py",
    "sandbox/backends/passthrough.py",
    "voice/dictate.py",
    "voice/chat_bridge.py",
    "edits/transaction.py",
    "edits/wal.py",
    "refactor/python_rename.py",
    "refactor/js_rename.py",
    "swarm/runner.py",
    "selfdev/runner.py",
    "memory/importers/base.py",
    "memory/importers/claude_code.py",
    "memory/importers/codex.py",
    "memory/importers/opencode.py",
    "memory/importers/orchestrator.py",
    "plugins/mirror/client.py",
    "plugins/mirror/server.py",
    "plugins/mirror/serve_cli.py",
    "render/mermaid.py",
    "config/providers.py",
    "config/presets.py",
    "browser/tool.py",
    "pwa/api.py",
    "pwa/push.py",
    "pwa/manifest.py",
    "tui/streaming.py",
    "tui/progress.py",
    "tui/file_panel.py",
    "cli/bench.py",
    "cli/swarm_cmd.py",
    "cli/selfdev_cmd.py",
    "cli/dictate_cmd.py",
    "cli/memory_cmd.py",
    "cli/mcp_import_cmd.py",
    "cli/provider_cmd.py",
]

# Patterns that are unsafe on Windows.
_UNSAFE_OPEN_RE = re.compile(
    r"""
    \b(open|Path\([^)]*\)\.open|read_text|write_text)
    \s*\(
    [^)]*?                                 # allow other args first
    (
        # `mode='r'` / `'w'` / `'a'` — text mode patterns
        (mode\s*=\s*)?["'](?:r|w|a|rt|wt|at|r\+|w\+|a\+)["']
    )
    """,
    re.VERBOSE,
)
_HAS_ENCODING_RE = re.compile(r"encoding\s*=")


def _is_unsafe_line(line: str) -> bool:
    """Heuristic: text-mode open without 'encoding=' on the same line."""
    if "open(" not in line and "read_text" not in line and "write_text" not in line:
        return False
    if "encoding=" in line:
        return False
    if "rb" in line or "wb" in line or "ab" in line or 'mode="b"' in line:
        return False
    if line.lstrip().startswith("#"):
        return False
    # read_text() / write_text() with no encoding — flag it.
    if re.search(r"\.read_text\s*\(\s*\)", line):
        return True
    if re.search(r"\.write_text\s*\(", line) and "encoding" not in line:
        return True
    # open() in text mode without encoding.
    if re.search(r"open\s*\([^)]*['\"][rwat]\+?['\"]", line) and "encoding" not in line:
        return True
    return False


@pytest.mark.parametrize("rel_path", V1_MODULES)
def test_v1_module_uses_explicit_utf8_for_text_io(rel_path):
    src = PHANTOM_DIR / rel_path
    if not src.exists():
        pytest.skip(f"{rel_path} not present in this build")
    text = src.read_text(encoding="utf-8")
    offenders: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _is_unsafe_line(line):
            offenders.append((lineno, line.strip()))
    assert not offenders, (
        f"{rel_path} has text-mode I/O without explicit encoding=:\n"
        + "\n".join(f"  line {n}: {ln}" for n, ln in offenders)
        + "\n\nFix: pass encoding=\"utf-8\" to open() / read_text() / "
          "write_text()."
    )


def test_v1_modules_all_present():
    """Sanity: every entry in V1_MODULES exists. Catches typos in the list."""
    missing = [m for m in V1_MODULES if not (PHANTOM_DIR / m).exists()]
    assert not missing, f"V1_MODULES references missing files: {missing}"
