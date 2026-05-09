"""Tests for v1.1.19 host-OS shell guidance injection.

Triggered by the v1.1.18 user report: "all bash commands are not
executing properly" — model emitted POSIX commands like `mkdir -p`
and `2>&1 | tail -20` that cmd.exe on Windows can't understand, so
they failed silently. The system prompt now tells the model what
host OS it's on and what shell quirks apply.
"""

from __future__ import annotations

from unittest.mock import patch

from phantom.cli.chat import (
    _os_shell_guidance,
    _personalize_system_prompt,
    _POSIX_SHELL_GUIDANCE,
    _WINDOWS_SHELL_GUIDANCE,
)
from phantom.profile import Profile


# ─── _os_shell_guidance returns the right paragraph per host ────────────────

def test_os_guidance_windows():
    with patch("platform.system", return_value="Windows"):
        out = _os_shell_guidance()
    assert "cmd.exe" in out
    assert "mkdir -p" in out and "DO NOT" in out
    assert "start /b" in out
    # Header line names Windows so the model picks the right syntax mode.
    assert out.startswith("Host OS: Windows")


def test_os_guidance_linux():
    with patch("platform.system", return_value="Linux"):
        out = _os_shell_guidance()
    assert "Linux" in out or "POSIX" in out
    assert "/bin/sh" in out
    assert "nohup" in out


def test_os_guidance_macos():
    with patch("platform.system", return_value="Darwin"):
        out = _os_shell_guidance()
    assert "Darwin" in out or "POSIX" in out
    assert "/bin/sh" in out


# ─── personalize_system_prompt always includes the OS line ──────────────────

def test_personalize_appends_os_guidance_for_windows(tmp_path):
    with patch("platform.system", return_value="Windows"):
        out = _personalize_system_prompt(
            "You are Phantom, a local coding agent.",
            Profile(user_name="A", assistant_name="P", workspace_path="/x"),
        )
    assert "Host OS: Windows" in out
    assert "cmd.exe" in out
    assert "/x" in out  # workspace still injected
    assert "name is A" in out  # user name still injected


def test_personalize_appends_os_guidance_for_linux():
    with patch("platform.system", return_value="Linux"):
        out = _personalize_system_prompt(
            "You are Phantom, a local coding agent.",
            Profile(),
        )
    assert "Host OS: Linux" in out
    assert "POSIX" in out or "/bin/sh" in out


def test_windows_guidance_warns_against_posix_pipes():
    """The exact failure pattern from the user trace:
    `cd ... && pip install flask 2>&1 | tail -20`. Windows guidance
    must explicitly call out that `tail`/`head`/`grep` aren't there."""
    assert "tail" in _WINDOWS_SHELL_GUIDANCE
    assert "head" in _WINDOWS_SHELL_GUIDANCE or "findstr" in _WINDOWS_SHELL_GUIDANCE


def test_windows_guidance_recommends_write_file_for_dirs():
    """`mkdir -p` was the most-emitted broken command in the user trace.
    Guidance should redirect the model to write_file (which auto-creates
    parents) for the common case."""
    assert "mkdir -p" in _WINDOWS_SHELL_GUIDANCE
    assert "write_file" in _WINDOWS_SHELL_GUIDANCE
