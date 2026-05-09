"""Tests for v1.1.17 slash commands: /preset, /presets, /voice, /dictate,
/dashboard, /doctor, /plugins.

Triggered by the v1.1.16 user report: hidden features (voice / dashboard
/ plugins / doctor) and curated provider presets (openrouter, etc.) were
unreachable from inside chat. The user wanted to switch to OpenRouter
without leaving the REPL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phantom.agent import AgentSession, ScriptedProvider
from phantom.cli.chat import _handle_slash
from phantom.config.providers import ProviderRegistry


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    # Wipe any preset env keys so registration falls into the prompt path
    # (we don't actually reach the prompt in tests because we provide a
    # blank api_key — but we want consistency).
    for k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _scripted_session():
    return AgentSession(provider=ScriptedProvider(), tools=[])


def _capture():
    out: list[str] = []
    return out, out.append


# ─── /presets (plural) — list ────────────────────────────────────────────────

def test_slash_presets_lists_all(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/presets", arg="", write=write)
    text = "".join(out)
    # Sanity: the well-known names are listed.
    for name in ("nvidia", "groq", "openrouter", "together", "ollama"):
        assert name in text


def test_slash_preset_with_no_arg_lists(home):
    """`/preset` (no arg) is treated as `/presets` so the user discovers
    options without remembering the plural."""
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/preset", arg="", write=write)
    text = "".join(out)
    assert "openrouter" in text


# ─── /preset <name> — register ──────────────────────────────────────────────

def test_slash_preset_registers_local_only_no_key_prompt(home, monkeypatch):
    """ollama/lmstudio/vllm-local don't need a key — registration must not
    block on a prompt."""
    # If a prompt fires it would call typer.prompt which reads stdin; in
    # tests stdin is not a TTY so it'd raise. The handler must skip the
    # prompt entirely for local-only presets.
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/preset", arg="ollama", write=write)
    saved = ProviderRegistry.load().list()
    assert any(p.name == "ollama" for p in saved)


def test_slash_preset_unknown_name_warns(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/preset", arg="not-a-preset", write=write)
    assert "unknown preset" in "".join(out).lower()
    assert ProviderRegistry.load().list() == []


def test_slash_preset_with_env_key_skips_prompt(home, monkeypatch):
    """If OPENROUTER_API_KEY is exported, `/preset openrouter` registers
    without prompting — use the env key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/preset", arg="openrouter", write=write)
    saved = ProviderRegistry.load().get("openrouter")
    assert saved is not None
    assert saved.api_key_env == "OPENROUTER_API_KEY"
    # Inline key not stored — we use the env var.
    assert saved.api_key_inline == ""


# ─── /voice and /dictate ─────────────────────────────────────────────────────

def test_slash_voice_explains_how_to_run(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/voice", arg="", write=write)
    text = "".join(out)
    assert "phantom dictate" in text
    assert "Whisper" in text


def test_slash_dictate_is_alias_for_voice(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/dictate", arg="", write=write)
    assert "phantom dictate" in "".join(out)


# ─── /dashboard ──────────────────────────────────────────────────────────────

def test_slash_dashboard_explains_how_to_run(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/dashboard", arg="", write=write)
    text = "".join(out)
    assert "phantom dashboard" in text
    assert "127.0.0.1:8000" in text or "8000" in text


# ─── /doctor ─────────────────────────────────────────────────────────────────

def test_slash_doctor_runs_inline_capability_report(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/doctor", arg="", write=write)
    text = "".join(out)
    # The report mentions the sandbox section regardless of which backend
    # is actually selected on this host.
    assert "sandbox" in text.lower()


# ─── /plugins ────────────────────────────────────────────────────────────────

def test_slash_plugins_runs_without_crash(home):
    """Plugin discovery may find zero plugins in a fresh install — the
    handler must surface a clean message either way."""
    session = _scripted_session()
    out, write = _capture()
    handled = _handle_slash(session=session, head="/plugins", arg="", write=write)
    assert handled is True
    # Either lists plugins (header has 'capabilities' / 'enabled') or
    # reports none. Never a stack trace.
    text = "".join(out).lower()
    assert (
        "capabilities" in text       # header line of the list
        or "no plugins" in text      # empty install
        or "plugin" in text
    )


# ─── /help lists the new commands ────────────────────────────────────────────

def test_help_lists_all_v1_1_17_commands(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/help", arg="", write=write)
    text = "".join(out)
    for cmd in ("/preset", "/presets", "/voice", "/dashboard", "/doctor", "/plugins"):
        assert cmd in text, f"{cmd} missing from /help"
