"""Tests for profile, onboarding, boot banner, sysinfo, and the new
v1.1.10 slash commands (/name, /workspace, /system, /memory, /buy,
/license, /god-mode, /uninstall)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.agent import AgentSession, ScriptedProvider
from phantom.cli.boot import onboard_if_needed, render_boot_banner
from phantom.cli.chat import _handle_slash, _SLASH_EXIT
from phantom.cli.sysinfo import collect_system_info
from phantom.profile import Profile, load_profile, profile_path, save_profile


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path


def _scripted_session():
    return AgentSession(provider=ScriptedProvider([]), tools=[])


def _capture():
    out: list[str] = []
    return out, out.append


# ─── Profile persistence ──────────────────────────────────────────────────────

def test_load_returns_empty_profile_when_missing(home: Path):
    p = load_profile()
    assert p.user_name == ""
    assert p.assistant_name == "Phantom"
    assert p.workspace_path == ""


def test_save_then_load_roundtrip(home: Path):
    save_profile(Profile(user_name="Aravind", assistant_name="JARVIS",
                         workspace_path="/tmp/projects"))
    again = load_profile()
    assert again.user_name == "Aravind"
    assert again.assistant_name == "JARVIS"
    assert again.workspace_path == "/tmp/projects"
    assert again.first_seen != ""  # auto-stamped on save


def test_is_complete_only_when_all_fields_set(home: Path):
    p = Profile(user_name="A", assistant_name="P", workspace_path="")
    assert not p.is_complete()
    p.workspace_path = "/x"
    assert p.is_complete()


# ─── Onboarding ──────────────────────────────────────────────────────────────

def test_onboard_skips_when_profile_complete(home: Path):
    save_profile(Profile(
        user_name="Aravind", assistant_name="JARVIS",
        workspace_path=str(home),
    ))
    inputs = iter([])  # would raise if asked
    out, write = _capture()
    p = onboard_if_needed(
        write=write, read_line=lambda prompt: next(inputs),
    )
    assert p.user_name == "Aravind"


def test_onboard_collects_all_three_fields(home: Path, tmp_path: Path):
    workspace = tmp_path / "Projects"
    inputs = iter(["JARVIS", "Aravind", str(workspace)])
    out, write = _capture()
    p = onboard_if_needed(
        write=write, read_line=lambda prompt: next(inputs),
    )
    assert p.assistant_name == "JARVIS"
    assert p.user_name == "Aravind"
    assert p.workspace_path == str(workspace)
    assert workspace.exists()  # created if missing
    # Persisted to disk.
    persisted = json.loads(profile_path().read_text())
    assert persisted["user_name"] == "Aravind"


def test_onboard_uses_default_when_user_hits_enter(home: Path):
    inputs = iter(["", "Aravind", ""])  # default name, set user, default workspace
    out, write = _capture()
    p = onboard_if_needed(
        write=write, read_line=lambda prompt: next(inputs),
    )
    assert p.assistant_name == "Phantom"
    assert p.workspace_path != ""  # default suggestion was accepted


# ─── sysinfo ──────────────────────────────────────────────────────────────────

def test_sysinfo_returns_filled_struct(home: Path):
    info = collect_system_info()
    assert info.os_name in ("Linux", "Darwin", "Windows", "")
    assert info.cpu_count >= 1
    assert info.hostname  # always non-empty on Linux/macOS/Windows


# ─── boot banner ──────────────────────────────────────────────────────────────

def test_boot_banner_includes_user_name_when_set(home: Path):
    profile = Profile(user_name="Aravind", assistant_name="JARVIS", workspace_path="/tmp")
    info = collect_system_info()
    out, write = _capture()
    render_boot_banner(write=write, profile=profile, system=info, animate=False)
    text = "".join(out)
    assert "Aravind" in text
    assert "JARVIS" in text
    assert info.hostname in text


def test_boot_banner_works_without_user_name(home: Path):
    profile = Profile(user_name="", assistant_name="Phantom", workspace_path="")
    info = collect_system_info()
    out, write = _capture()
    render_boot_banner(write=write, profile=profile, system=info, animate=False)
    text = "".join(out)
    assert "Phantom" in text
    # No "Welcome back, " when user_name is empty.
    assert "Welcome back" not in text


# ─── /name ────────────────────────────────────────────────────────────────────

def test_slash_name_no_arg_shows_current(home: Path):
    save_profile(Profile(user_name="A", assistant_name="JARVIS", workspace_path="/x"))
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/name", arg="", write=write)
    assert "JARVIS" in "".join(out)


def test_slash_name_renames_assistant(home: Path):
    save_profile(Profile(user_name="A", assistant_name="Phantom", workspace_path="/x"))
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/name", arg="JARVIS", write=write)
    assert load_profile().assistant_name == "JARVIS"


# ─── /workspace ───────────────────────────────────────────────────────────────

def test_slash_workspace_sets_path(home: Path, tmp_path: Path):
    target = tmp_path / "MyProjects"
    save_profile(Profile(user_name="A", assistant_name="P", workspace_path="/x"))
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/workspace", arg=str(target), write=write)
    assert load_profile().workspace_path == str(target)
    assert target.exists()


# ─── /system ──────────────────────────────────────────────────────────────────

def test_slash_system_shows_host_info(home: Path):
    save_profile(Profile(user_name="A", assistant_name="P", workspace_path=str(home)))
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/system", arg="", write=write)
    text = "".join(out)
    assert "host" in text.lower()
    assert "os" in text.lower()


# ─── /buy + /license ──────────────────────────────────────────────────────────

def test_slash_buy_prints_url(home: Path):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/buy", arg="", write=write)
    assert "phantom.aravindlabs.tech/buy" in "".join(out)


def test_slash_install_license_without_arg_shows_usage(home: Path):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/install-license", arg="", write=write)
    assert "PHC-" in "".join(out)


# ─── /god-mode ────────────────────────────────────────────────────────────────

def test_slash_god_mode_toggle_persists_to_profile(home: Path):
    save_profile(Profile(user_name="A", assistant_name="P", workspace_path="/x"))
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/god-mode", arg="on", write=write)
    assert load_profile().god_mode is True
    _handle_slash(session=session, head="/god-mode", arg="off", write=write)
    assert load_profile().god_mode is False


def test_slash_god_mode_modifies_system_prompt(home: Path):
    save_profile(Profile(user_name="A", assistant_name="P", workspace_path="/x"))
    session = _scripted_session()
    original = session.system_prompt
    out, write = _capture()
    _handle_slash(session=session, head="/god-mode", arg="on", write=write)
    assert "GOD-MODE" in session.system_prompt
    _handle_slash(session=session, head="/god-mode", arg="off", write=write)
    assert "GOD-MODE" not in session.system_prompt


# ─── /memory ──────────────────────────────────────────────────────────────────

def test_slash_memory_no_arg_reports_count(home: Path):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/memory", arg="", write=write)
    text = "".join(out)
    assert "memory" in text.lower()


# ─── /uninstall ──────────────────────────────────────────────────────────────

def test_slash_uninstall_without_confirm_warns_only(home: Path):
    save_profile(Profile(user_name="A", assistant_name="P", workspace_path="/x"))
    session = _scripted_session()
    out, write = _capture()
    rc = _handle_slash(session=session, head="/uninstall", arg="", write=write)
    assert rc is True  # not _SLASH_EXIT — just a warning
    assert profile_path().exists()  # profile still there


def test_slash_uninstall_with_yes_removes_phantom_home(home: Path):
    save_profile(Profile(user_name="A", assistant_name="P", workspace_path="/x"))
    session = _scripted_session()
    out, write = _capture()
    rc = _handle_slash(session=session, head="/uninstall", arg="--yes", write=write)
    assert rc is _SLASH_EXIT
    assert not home.exists()  # rmtree'd


# ─── tool error robustness ────────────────────────────────────────────────────

def test_write_file_empty_path_returns_hint_not_exception():
    """The model previously got a one-line PhantomError; now it gets actionable JSON."""
    from phantom.agent.tools import _write_file
    result = _write_file({}, allowlist=("/tmp",))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "hint" in parsed
    assert "Example arguments" in parsed["hint"]


def test_read_file_empty_path_returns_hint():
    from phantom.agent.tools import _read_file
    result = _read_file({"path": ""}, allowlist=("/tmp",))
    parsed = json.loads(result)
    assert parsed["error"].startswith("read_file")
    assert "Example arguments" in parsed["hint"]
