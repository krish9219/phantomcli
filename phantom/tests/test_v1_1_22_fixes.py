"""Tests for v1.1.22 — the fixes called out by the user's 10-prompt
test run.

1. /model arg parser stops at whitespace (was: consumed entire trailing
   sentence as a single model id).
2. start_server auto-port: requested port in use → bumps to next free.
3. _rewrite_port: --port=N / -p N / :N rewrite + env-var fallback.
4. Identity anchor: name substitution + "never reveal underlying model"
   wording in personalised system prompt.
5. Memory nudge: prompt explicitly tells the model to call memory_add
   on "remember that …" requests.
6. /telegram slash command surfaces the existing v3 bot.
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from phantom.agent import AgentSession, ScriptedProvider
from phantom.agent.tools import _is_port_in_use, _rewrite_port
from phantom.cli.chat import _handle_slash, _personalize_system_prompt
from phantom.config.providers import CustomProvider, ProviderRegistry
from phantom.profile import Profile


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path


def _scripted_session():
    return AgentSession(provider=ScriptedProvider(), tools=[])


def _capture():
    out: list[str] = []
    return out, out.append


# ─── /model arg parser stops at whitespace ──────────────────────────────────

def test_slash_model_strips_trailing_garbage(home):
    """The exact bug: `/model meta/llama-3.3-70b-instruct" then ask "..."`
    used to register the whole trailing sentence as a model id. Now we
    take only the first whitespace-delimited token."""
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(
        name="default", base_url="https://x.test/v1",
        model="dummy", api_key_inline="k",
    ))
    session = _scripted_session()
    # Give the session a real provider so the model-id fallback fires.
    from phantom.agent.provider import OpenAICompatibleProvider
    session.provider = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="dummy",
    )
    out, write = _capture()
    _handle_slash(
        session=session,
        head="/model",
        arg='meta/llama-3.3-70b-instruct" then ask "explain in 3 sentences why ...',
        write=write,
    )
    # The session was switched to the clean model id, not the garbage.
    assert session.provider._model == "meta/llama-3.3-70b-instruct"


def test_slash_model_strips_quote_wrapping(home):
    """`/model "meta/llama"` should register `meta/llama`, not `"meta/llama"`."""
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(
        name="default", base_url="https://x.test/v1",
        model="dummy", api_key_inline="k",
    ))
    from phantom.agent.provider import OpenAICompatibleProvider
    session = _scripted_session()
    session.provider = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="dummy",
    )
    out, write = _capture()
    _handle_slash(
        session=session, head="/model",
        arg='"meta/llama-3.3-70b-instruct"', write=write,
    )
    assert session.provider._model == "meta/llama-3.3-70b-instruct"


# ─── _rewrite_port flag substitution ─────────────────────────────────────────

@pytest.mark.parametrize("cmd,old,new,expected", [
    # Common Flask / Django / uvicorn flag forms.
    ("uvicorn main:app --port 8000", 8000, 8001, "uvicorn main:app --port=8001"),
    ("uvicorn main:app --port=8000", 8000, 8001, "uvicorn main:app --port=8001"),
    ("flask run -p 5000", 5000, 5050, "flask run -p 5050"),
    ("python app.py --port=5000", 5000, 5001, "python app.py --port=5001"),
    # Host:port style.
    ("./server --listen 127.0.0.1:5000", 5000, 5050, "./server --listen 127.0.0.1:5050"),
    # No matching pattern → env-var prefix is added.
    ("python app.py", 5000, 5001, None),  # check separately below
])
def test_rewrite_port_flag_forms(cmd, old, new, expected):
    out = _rewrite_port(cmd, old, new, new)
    if expected is None:
        # Fall-through: env-var wrapper. Both Windows + POSIX paths add
        # PORT={new} so the framework can pick it up.
        assert f"PORT={new}" in out
        assert "python app.py" in out
    else:
        assert out == expected


def test_rewrite_port_does_not_touch_unrelated_numbers():
    """`python -c 'print(5000)'` shouldn't be rewritten if 5000 is the
    requested port — we don't want code constants mangled."""
    cmd = "python -c \"print(5000)\""
    out = _rewrite_port(cmd, 5000, 5001, 5001)
    # Falls through to env-var wrapper (no flag-style match), so the
    # original print(5000) survives untouched.
    assert "print(5000)" in out


# ─── _is_port_in_use ────────────────────────────────────────────────────────

def test_is_port_in_use_detects_listener():
    """Bind a socket and confirm the helper sees it as in-use."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert _is_port_in_use(port) is True
    finally:
        s.close()


def test_is_port_in_use_returns_false_for_free_port():
    # Find a port, close the socket, then probe.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert _is_port_in_use(port) is False


# ─── identity anchor in system prompt ───────────────────────────────────────

def test_personalize_includes_strict_identity_anchor():
    """The Ling/qwen leak fix: prompt tells the model never to reveal
    the underlying model brand."""
    prompt = "You are Phantom, a coding agent."
    out = _personalize_system_prompt(
        prompt, Profile(user_name="A", assistant_name="Ghost", workspace_path="/x"),
    )
    low = out.lower()
    assert "your name is ghost" in low
    # The strict block tells the model not to reveal model brand.
    assert "never" in low
    assert "model" in low
    # The original "You are Phantom" line gets the name swapped.
    assert "You are Ghost," in out


def test_personalize_default_assistant_name_keeps_phantom():
    """If user kept the default assistant_name=Phantom, the identity
    block still appears but mentions Phantom (not a different name)."""
    prompt = "You are Phantom, a coding agent."
    out = _personalize_system_prompt(prompt, Profile())
    assert "Your name is Phantom" in out


# ─── memory nudge ──────────────────────────────────────────────────────────

def test_personalize_includes_memory_call_nudge():
    """The v1.1.21 'remember that I prefer X' didn't trigger memory_add.
    The persona prompt now tells the model to call the tool immediately."""
    prompt = "You are Phantom, a coding agent."
    out = _personalize_system_prompt(prompt, Profile())
    assert "memory_add" in out
    assert "remember" in out.lower()


# ─── /telegram slash ───────────────────────────────────────────────────────

def test_slash_telegram_explains_setup(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/telegram", arg="", write=write)
    text = "".join(out)
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "BotFather" in text
    assert "phantom telegram" in text


def test_help_lists_telegram(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/help", arg="", write=write)
    assert "/telegram" in "".join(out)
