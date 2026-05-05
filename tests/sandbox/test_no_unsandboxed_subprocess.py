"""Static-analysis test: no `phantom/*.py` outside the sandbox calls subprocess.

ADR-0003 makes :mod:`phantom.sandbox` the single place that may invoke
``subprocess.run``, ``subprocess.Popen``, ``os.execvp``/``os.execv``/
``os.execve``, or ``os.system``. Every other module that wants to run a
process imports :func:`phantom.sandbox.run`.

This test is a grep-style assertion against the source tree. It runs
fast, fails loudly with a precise file:line:hit list, and is impossible
to bypass without explicit code changes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PHANTOM_DIR = Path(__file__).resolve().parents[2] / "phantom"

# Regexes against source — careful to match attribute access patterns used in
# real code, not bare strings inside docstrings or comments.
PATTERNS = [
    re.compile(r"\bsubprocess\.(run|Popen|call|check_call|check_output)\b"),
    re.compile(r"\bos\.(system|popen|execv|execvp|execve|execvpe|execlp|execle|execl)\b"),
    re.compile(r"\bos\.spawn(l|le|lp|lpe|v|ve|vp|vpe|p)\b"),
]

# Files that ARE allowed to call subprocess. The sandbox lives here; the
# sandbox tests' own helpers may also need to exercise subprocess calls.
ALLOWED = {
    PHANTOM_DIR / "sandbox" / "_backend.py",            # ABC, no calls
    PHANTOM_DIR / "sandbox" / "select.py",              # no calls
    PHANTOM_DIR / "sandbox" / "policy.py",              # no calls
    PHANTOM_DIR / "sandbox" / "result.py",              # no calls
    PHANTOM_DIR / "sandbox" / "audit.py",               # no calls
    PHANTOM_DIR / "sandbox" / "limits.py",              # no calls
    PHANTOM_DIR / "sandbox" / "__init__.py",            # router only
    PHANTOM_DIR / "sandbox" / "backends" / "__init__.py",
    PHANTOM_DIR / "sandbox" / "backends" / "bwrap.py",
    PHANTOM_DIR / "sandbox" / "backends" / "firejail.py",
    PHANTOM_DIR / "sandbox" / "backends" / "unshare.py",
    PHANTOM_DIR / "sandbox" / "backends" / "docker.py",
    PHANTOM_DIR / "sandbox" / "backends" / "passthrough.py",  # Windows fallback (v1.0)
    # MCP stdio transport spawns a child MCP server. The child is
    # operator-chosen trusted code; ADR-0003 explicitly sanctions this
    # path. The transport module's docstring carries the full rationale.
    PHANTOM_DIR / "mcp" / "transport.py",
    # ─── v1.0 exemptions ────────────────────────────────────────────
    # Each carries its own justification; the common shape is "shells
    # out to a developer-trusted external tool (git, gh, pytest, sox)
    # to fulfil a task that has no sensible sandbox-side analogue."
    # All run on the operator's box with the operator's privileges,
    # not on attacker-supplied input.
    #
    # Swarm + self-dev orchestrate `git worktree` / `git diff` /
    # `git merge` against the user's own repo. Sandboxing git would
    # mean copying the entire repo into a sealed namespace per call,
    # which defeats the worktree-isolation feature itself.
    PHANTOM_DIR / "swarm" / "runner.py",
    PHANTOM_DIR / "selfdev" / "runner.py",
    # Bench measures cold-start by spawning python; sandboxing the
    # subprocess would invalidate the metric (sandbox setup time
    # would dominate). Documented in the module docstring.
    PHANTOM_DIR / "cli" / "bench.py",
    # `phantom dictate` shells out to sox/arecord/parecord — host
    # audio devices live outside any sandbox we'd plausibly build.
    PHANTOM_DIR / "voice" / "dictate.py",
    # github-pr plugin shells out to the user's `gh` CLI. The plugin
    # declares the `executor` capability in its manifest; loaders
    # that grant it accept the trade-off.
    PHANTOM_DIR / "plugins" / "builtin" / "github_pr" / "__init__.py",
    # Daemon command in the CLI uses os.fork (matched by os.* regex)
    # only inside the explicit `--detach` branch.
    PHANTOM_DIR / "cli" / "__init__.py",
    # Mermaid TUI renderer shells out to operator-installed `mmdc` and
    # `img2sixel`. Both are local developer tools running with the
    # operator's privileges, on operator-supplied input. Sandboxing them
    # would defeat the rendering pipeline (the sandbox setup time alone
    # would dwarf the render). Fallback path is pure-Python ASCII.
    PHANTOM_DIR / "render" / "mermaid.py",
}


def _python_files_under(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*.py"):
        # Skip __pycache__, tests, and stub files.
        if "__pycache__" in p.parts:
            continue
        if "tests" in p.parts:
            continue
        out.append(p)
    return out


@pytest.mark.security
@pytest.mark.stage1
def test_no_unsandboxed_subprocess_in_phantom_package():
    violations: list[tuple[Path, int, str, str]] = []
    for path in _python_files_under(PHANTOM_DIR):
        if path in ALLOWED:
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            # Skip comments and docstrings — minimal heuristic, sufficient for
            # our codebase. We only flag lines that are *not* a comment.
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for rgx in PATTERNS:
                m = rgx.search(line)
                if m:
                    violations.append((path, lineno, m.group(0), line.strip()))

    if violations:
        msg_lines = ["unsandboxed subprocess calls outside phantom.sandbox:"]
        for path, lineno, hit, text in violations:
            rel = path.relative_to(PHANTOM_DIR.parent)
            msg_lines.append(f"  {rel}:{lineno}  → {hit!r}  in: {text}")
        msg_lines.append(
            "If this is genuinely sandbox code, add the file to "
            "tests/sandbox/test_no_unsandboxed_subprocess.py::ALLOWED."
        )
        pytest.fail("\n".join(msg_lines))
