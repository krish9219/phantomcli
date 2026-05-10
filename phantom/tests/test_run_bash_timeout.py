"""Tests for v1.1.18: run_bash 60s default timeout + server-start hint.

Triggered by the v1.1.17 user report: `python app.py` started Flask in
the foreground and the agent stayed locked for 25 minutes because the
old 300s default per-call timeout meant each tool call could block 5
minutes, and the model retried multiple times.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.agent.tools import (
    _looks_like_server_start,
    _run_bash,
    default_tools,
)


# ─── _looks_like_server_start ────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "python app.py",
    "cd app && python app.py",
    "python -m flask run",
    "flask run",
    "uvicorn main:app --reload",
    "gunicorn -w 4 app:app",
    "npm start",
    "npm run dev",
    "pnpm start",
    "yarn dev",
    "node server.js",
    "node index.mjs",
    "next dev",
    "next start",
    "rails server",
])
def test_detects_known_server_starts(cmd):
    assert _looks_like_server_start(cmd) is True


@pytest.mark.parametrize("cmd", [
    "python --version",
    "pip install flask",
    "ls -la",
    "mkdir -p ./out",
    "python -c 'print(1)'",        # one-liner, not a script
])
def test_does_not_flag_normal_commands(cmd):
    assert _looks_like_server_start(cmd) is False


# ─── _run_bash defaults ──────────────────────────────────────────────────────

def test_run_bash_default_timeout_is_60s(tmp_path: Path):
    """A short command runs to completion well within the default."""
    result = json.loads(_run_bash({"command": "echo hi"}, workdir=str(tmp_path)))
    assert result["exit_code"] == 0
    assert "hi" in result["stdout"]
    # Wall time was tiny — far below 60s. Sanity check.
    assert result["wall_s"] < 5.0


def test_run_bash_explicit_timeout_is_clamped(tmp_path: Path):
    """User-supplied timeout is clamped to [1, 600]."""
    # Negative → clamped up to 1. Run an immediate-exit command.
    result = json.loads(_run_bash(
        {"command": "echo ok", "timeout": -5},
        workdir=str(tmp_path),
    ))
    assert result["exit_code"] == 0


def test_run_bash_empty_command_returns_hint_not_exception(tmp_path: Path):
    """Match the v1.1.10 fix path: bad args return JSON with hint, not raise."""
    result = json.loads(_run_bash({"command": ""}, workdir=str(tmp_path)))
    assert "error" in result
    assert "hint" in result
    assert "command" in result["hint"]


# ─── server-start hint ───────────────────────────────────────────────────────

def test_run_bash_appends_server_hint_when_command_times_out(tmp_path: Path):
    """When a server-start command runs to the wall-clock cap, the result
    grows a `hint` field telling the model to background next time."""
    # `sleep 2` doesn't match server pattern, so use a real Python script
    # that emulates a blocking server.
    script = tmp_path / "fake_server.py"
    script.write_text("import time\ntime.sleep(60)\n")
    # Docker-sandbox tier needs the workdir + script world-readable: GitHub
    # CI runners create pytest-of-runner/* with mode 700 by default, which
    # the docker container's root user can't traverse. Mode 755 + 644 lets
    # the sandbox container read the script. (Same fix already applied to
    # the other docker-tier tests in v1.0.2.)
    import os as _os
    _os.chmod(tmp_path, 0o755)
    script.chmod(0o644)
    result = json.loads(_run_bash(
        {"command": f"python {script.name}", "timeout": 2},
        workdir=str(tmp_path),
    ))
    assert "hint" in result
    assert "background" in result["hint"].lower()
    assert "start /b" in result["hint"] or "nohup" in result["hint"]


def test_run_bash_no_hint_for_normal_commands(tmp_path: Path):
    """A short non-server command must not get the server hint."""
    result = json.loads(_run_bash(
        {"command": "echo done"},
        workdir=str(tmp_path),
    ))
    assert "hint" not in result


# ─── tool registration: schema reflects the new defaults ─────────────────────

def test_run_bash_tool_schema_advertises_new_defaults(tmp_path: Path):
    tools = default_tools(workdir=str(tmp_path))
    bash = next(t for t in tools if t.name == "run_bash")
    assert "timeout" in bash.input_schema["properties"]
    timeout_schema = bash.input_schema["properties"]["timeout"]
    assert timeout_schema["default"] == 60
    desc = bash.description.lower()
    assert "60 seconds" in desc or "60-second" in desc or "default 60" in desc
    # v1.1.20: rather than backgrounding via `nohup`/`start /b`, tell the
    # model to use the dedicated start_server tool.
    assert "start_server" in desc
    assert "do not use this for long-running servers" in desc
