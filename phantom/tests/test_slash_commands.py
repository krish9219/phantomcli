"""Tests for the chat REPL slash commands and tool-history scrubbing."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from phantom.agent import AgentSession, ScriptedProvider
from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
    ProviderResponse,
)
from phantom.cli.chat import _handle_slash, _SLASH_EXIT, run_repl, _is_smart
from phantom.config.providers import CustomProvider, ProviderRegistry


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path


def _scripted_session(replies: list[ProviderResponse]) -> AgentSession:
    return AgentSession(provider=ScriptedProvider(replies), tools=[])


def _capture():
    out: list[str] = []
    return out, out.append


# ─── tool-history scrubbing (the v1.1.7 NVIDIA 400 bug) ──────────────────────

def _ok_response(text: str = "ok"):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "choices": [{
            "message": {"content": text, "tool_calls": []},
            "finish_reason": "stop",
        }],
    }
    return r


def test_no_tools_strips_orphan_tool_messages():
    """The exact NVIDIA 400 bug: history has a tool message left over from a
    prior turn that fell back to chat-only. Provider must scrub it."""
    client = MagicMock()
    client.post = MagicMock(side_effect=[_ok_response("hi")])
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
        client=client, tools_supported=False,
    )
    messages = [
        ProviderMessage(role="system", content="be brief"),
        ProviderMessage(role="user", content="first turn"),
        ProviderMessage(role="assistant", content=""),  # was a tool-call wrapper
        ProviderMessage(role="tool", content="result", tool_call_id="t1", name="x"),
        ProviderMessage(role="assistant", content="here you go"),
        ProviderMessage(role="user", content="second turn"),
    ]
    p.complete(messages, tools=[])
    sent = client.post.call_args.kwargs["json"]["messages"]
    roles = [m["role"] for m in sent]
    assert "tool" not in roles
    # The orphan empty assistant message should also be gone.
    assert not any(m["role"] == "assistant" and not m["content"].strip() for m in sent)
    # Real assistant turns survive.
    assert any(m["content"] == "here you go" for m in sent)
    assert sent[-1]["content"] == "second turn"


def test_with_tools_keeps_full_history():
    """With tools enabled, the legitimate tool messages are preserved."""
    client = MagicMock()
    client.post = MagicMock(side_effect=[_ok_response("ok")])
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
        client=client, tools_supported=True,
    )
    messages = [
        ProviderMessage(role="user", content="x"),
        ProviderMessage(role="tool", content="r", tool_call_id="t1", name="t"),
    ]
    tools = [{"type": "function", "function": {"name": "t", "description": "x", "parameters": {}}}]
    p.complete(messages, tools=tools)
    sent = client.post.call_args.kwargs["json"]["messages"]
    roles = [m["role"] for m in sent]
    assert "tool" in roles


# ─── /help, /reset, /history ──────────────────────────────────────────────────

def test_slash_help_lists_all_commands(home):
    session = _scripted_session([])
    out, write = _capture()
    handled = _handle_slash(session=session, head="/help", arg="", write=write)
    assert handled is True
    text = "".join(out)
    for cmd in ("/model", "/models", "/add", "/smart", "/reset", "/history", "/exit"):
        assert cmd in text


def test_slash_reset_clears_history(home):
    session = _scripted_session([])
    session.history.append(ProviderMessage(role="user", content="x"))
    session.history.append(ProviderMessage(role="assistant", content="y"))
    out, write = _capture()
    _handle_slash(session=session, head="/reset", arg="", write=write)
    assert len(session.history) == 0


def test_slash_history_shows_length(home):
    session = _scripted_session([])
    for i in range(3):
        session.history.append(ProviderMessage(role="user", content=str(i)))
    out, write = _capture()
    _handle_slash(session=session, head="/history", arg="", write=write)
    assert "3" in "".join(out)


def test_slash_exit_returns_sentinel(home):
    session = _scripted_session([])
    out, write = _capture()
    rc = _handle_slash(session=session, head="/exit", arg="", write=write)
    assert rc is _SLASH_EXIT


# ─── /models, /providers ──────────────────────────────────────────────────────

def test_slash_models_empty_registry_prints_hint(home):
    session = _scripted_session([])
    out, write = _capture()
    _handle_slash(session=session, head="/models", arg="", write=write)
    assert "no providers" in "".join(out).lower()


def test_slash_models_lists_registered(home):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a.test/v1", model="m1"))
    reg.add(CustomProvider(name="beta", base_url="https://b.test/v1", model="m2"))
    session = _scripted_session([])
    out, write = _capture()
    _handle_slash(session=session, head="/models", arg="", write=write)
    text = "".join(out)
    assert "alpha" in text
    assert "beta" in text
    assert "m1" in text


def test_slash_providers_is_alias_for_models(home):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a.test/v1", model="m1"))
    session = _scripted_session([])
    out, write = _capture()
    _handle_slash(session=session, head="/providers", arg="", write=write)
    assert "alpha" in "".join(out)


# ─── /model ───────────────────────────────────────────────────────────────────

def test_slash_model_no_arg_shows_current(home):
    session = _scripted_session([])
    out, write = _capture()
    _handle_slash(session=session, head="/model", arg="", write=write)
    assert "current" in "".join(out).lower()


def test_slash_model_unknown_name_lists_options(home):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a.test/v1", model="m1"))
    session = _scripted_session([])
    out, write = _capture()
    _handle_slash(session=session, head="/model", arg="ghost", write=write)
    text = "".join(out)
    assert "unknown" in text.lower()
    assert "alpha" in text


def test_slash_model_switches_provider(home):
    """`/model <name>` rebuilds session.provider against the registered entry."""
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(
        name="alpha", base_url="https://a.test/v1", model="alpha-model",
        api_key_inline="ak",
    ))
    session = _scripted_session([])
    out, write = _capture()
    _handle_slash(session=session, head="/model", arg="alpha", write=write)
    assert isinstance(session.provider, OpenAICompatibleProvider)
    assert session.provider._model == "alpha-model"
    assert "switched" in "".join(out).lower()


def test_slash_model_switch_drops_orphan_tool_messages(home):
    """Switching providers must clear `tool` role messages so the new model
    doesn't immediately 400."""
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a.test/v1", model="m"))
    session = _scripted_session([])
    session.history.append(ProviderMessage(role="user", content="x"))
    session.history.append(ProviderMessage(role="tool", content="r", tool_call_id="t1", name="t"))
    out, write = _capture()
    _handle_slash(session=session, head="/model", arg="alpha", write=write)
    assert all(m.role != "tool" for m in session.history)


# ─── /smart ───────────────────────────────────────────────────────────────────

def test_slash_smart_default_off_then_toggle(home):
    session = _scripted_session([])
    out, write = _capture()
    assert _is_smart(session) is False
    _handle_slash(session=session, head="/smart", arg="on", write=write)
    assert _is_smart(session) is True
    _handle_slash(session=session, head="/smart", arg="off", write=write)
    assert _is_smart(session) is False


def test_slash_smart_no_arg_reports_current_state(home):
    session = _scripted_session([])
    out, write = _capture()
    _handle_slash(session=session, head="/smart", arg="", write=write)
    assert "off" in "".join(out).lower() or "smart" in "".join(out).lower()


def test_slash_smart_on_prepends_to_system_prompt(home):
    session = _scripted_session([])
    original = session.system_prompt
    out, write = _capture()
    _handle_slash(session=session, head="/smart", arg="on", write=write)
    assert session.system_prompt != original
    assert "expert engineer" in session.system_prompt.lower()
    # Toggle back: prompt restored exactly.
    _handle_slash(session=session, head="/smart", arg="off", write=write)
    assert session.system_prompt == original


# ─── End-to-end via run_repl ──────────────────────────────────────────────────

def test_run_repl_dispatches_slash_with_arg(home):
    """`/model alpha` (with arg) must reach the dispatcher, not be sent to LLM."""
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(
        name="alpha", base_url="https://a.test/v1", model="m",
        api_key_inline="k",
    ))
    session = _scripted_session([ProviderResponse(text="should-not-be-called")])
    inputs = deque(["/model alpha\n", "/exit\n"])
    out: list[str] = []
    rc = run_repl(
        session,
        read_line=lambda: inputs.popleft() if inputs else "",
        write=out.append,
    )
    assert rc == 0
    text = "".join(out)
    assert "switched" in text.lower()
    # The scripted reply was not consumed.
    assert "should-not-be-called" not in text
