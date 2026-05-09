"""Tests for the v1.1.11 fixes from the user's Ghost / Arvi Sir session.

1. The default system prompt hard-coded 'You are Phantom' so the model
   ignored the prepended persona line — `_personalize_system_prompt`
   now substitutes the user's chosen name in place.
2. moonshotai/kimi-k2.6 emits tool calls inside delimited text blocks
   instead of the OpenAI tool_calls array — `_extract_inline_tool_calls`
   pulls them out and the agent loop runs them.
3. The chat REPL hard-coded `you ›` and `phantom ›` — both now read
   from the saved profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.agent.provider import _extract_inline_tool_calls
from phantom.cli.chat import _personalize_system_prompt
from phantom.profile import Profile, save_profile


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path


# ─── _personalize_system_prompt ──────────────────────────────────────────────

def test_personalize_substitutes_assistant_name():
    prompt = "You are Phantom, a local coding agent. Be brief."
    profile = Profile(user_name="Aravind", assistant_name="Ghost", workspace_path="/x")
    out = _personalize_system_prompt(prompt, profile)
    assert "You are Ghost," in out
    # Original "Phantom" identity claim no longer there.
    assert "You are Phantom," not in out


def test_personalize_keeps_phantom_when_user_kept_default():
    prompt = "You are Phantom, a local coding agent."
    profile = Profile(user_name="Aravind", assistant_name="Phantom", workspace_path="/x")
    out = _personalize_system_prompt(prompt, profile)
    assert "You are Phantom," in out


def test_personalize_prepends_user_name_header():
    prompt = "You are Phantom, a local coding agent."
    profile = Profile(user_name="Aravind", assistant_name="Ghost", workspace_path="")
    out = _personalize_system_prompt(prompt, profile)
    assert out.startswith("The user's name is Aravind")


def test_personalize_prepends_workspace_when_set():
    prompt = "You are Phantom, a local coding agent."
    profile = Profile(user_name="", assistant_name="Phantom", workspace_path="/home/a/Projects")
    out = _personalize_system_prompt(prompt, profile)
    assert "/home/a/Projects" in out
    assert "Default workspace" in out


def test_personalize_with_blank_profile_returns_prompt_unchanged():
    prompt = "You are Phantom, a local coding agent."
    profile = Profile()
    out = _personalize_system_prompt(prompt, profile)
    assert out == prompt


def test_personalize_substitution_only_hits_first_occurrence():
    """The default prompt mentions 'Phantom' once at the top and again later
    as a product reference; we only substitute the first identity claim."""
    prompt = "You are Phantom, a coding agent.\n\nPhantom uses sandbox isolation."
    profile = Profile(user_name="A", assistant_name="Ghost", workspace_path="")
    out = _personalize_system_prompt(prompt, profile)
    assert "You are Ghost," in out
    assert "Phantom uses sandbox isolation." in out  # second mention preserved


# ─── _extract_inline_tool_calls (kimi/minimax format) ────────────────────────

def test_extract_kimi_run_bash_call():
    """The exact text from the user's v1.1.10 Ghost session."""
    text = (
        'Let me first set up the project directory.'
        '<|tool_calls_section_begin|><|tool_call_begin|>'
        'functions.run_bash:{"command": "mkdir -p /tmp/proj && echo done", "tier": "default"}'
        '<|tool_call_end|><|tool_calls_section_end|>'
    )
    calls, cleaned = _extract_inline_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "run_bash"
    assert calls[0].arguments["command"].startswith("mkdir -p")
    assert "<|tool_call" not in cleaned
    assert cleaned == "Let me first set up the project directory."


def test_extract_multiple_calls_in_one_block():
    text = (
        '<|tool_calls_section_begin|>'
        '<|tool_call_begin|>functions.write_file:{"path": "a.py", "text": "x"}<|tool_call_end|>'
        '<|tool_call_begin|>functions.run_bash:{"command": "ls"}<|tool_call_end|>'
        '<|tool_calls_section_end|>'
    )
    calls, cleaned = _extract_inline_tool_calls(text)
    assert len(calls) == 2
    assert calls[0].name == "write_file"
    assert calls[1].name == "run_bash"
    assert cleaned == ""


def test_extract_returns_empty_when_no_markers():
    text = "Just a regular reply with no tool calls."
    calls, cleaned = _extract_inline_tool_calls(text)
    assert calls == []
    assert cleaned == text  # unchanged


def test_extract_skips_malformed_json():
    text = (
        '<|tool_calls_section_begin|>'
        '<|tool_call_begin|>functions.run_bash:{not valid}<|tool_call_end|>'
        '<|tool_calls_section_end|>'
    )
    calls, cleaned = _extract_inline_tool_calls(text)
    assert calls == []
    assert "<|tool_call" not in cleaned  # markers still removed


def test_extract_handles_functions_prefix_optional():
    """Some models emit `run_bash:{...}` without the functions. prefix."""
    text = (
        '<|tool_calls_section_begin|>'
        '<|tool_call_begin|>run_bash:{"command": "pwd"}<|tool_call_end|>'
        '<|tool_calls_section_end|>'
    )
    calls, cleaned = _extract_inline_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "run_bash"


# ─── End-to-end: provider _parse picks up inline calls ──────────────────────

def test_provider_parse_extracts_inline_calls_when_no_tool_calls_field():
    from phantom.agent.provider import OpenAICompatibleProvider
    data = {
        "choices": [{
            "message": {
                "content": (
                    'Setting up the project.'
                    '<|tool_calls_section_begin|><|tool_call_begin|>'
                    'functions.run_bash:{"command": "ls"}'
                    '<|tool_call_end|><|tool_calls_section_end|>'
                ),
                "tool_calls": [],
            },
            "finish_reason": "tool_calls",
        }],
    }
    response = OpenAICompatibleProvider._parse(data)
    assert response.wants_tools  # has tool_calls now
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "run_bash"
    assert response.text == "Setting up the project."


def test_provider_parse_prefers_native_tool_calls_over_inline():
    """When the API returns a real tool_calls array, don't double-extract."""
    from phantom.agent.provider import OpenAICompatibleProvider
    data = {
        "choices": [{
            "message": {
                "content": "thinking...",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {
                        "name": "run_bash",
                        "arguments": json.dumps({"command": "true"}),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }
    response = OpenAICompatibleProvider._parse(data)
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_1"  # native id preserved
