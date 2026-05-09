"""Tests for v1.1.16: 429 retry, think-tag stripping, stronger system prompts.

Triggered by the v1.1.15 user session: even with dual-mode set up
correctly, the executor said "I'll create app.py..." without ever
calling write_file. Plus NVIDIA returned 429s and llama-3.3 leaked
`</think>` tags into output.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
)
from phantom.agent.session import DEFAULT_SYSTEM_PROMPT
from phantom.cli.chat import (
    _EXECUTOR_SYSTEM_PROMPT_PREFIX,
    _EXECUTOR_SYSTEM_PROMPT_SUFFIX,
    _strip_thinking_tags,
)
from phantom.errors import PhantomError


# ─── DEFAULT_SYSTEM_PROMPT now nudges the model to act, not narrate ─────────

def test_default_prompt_has_act_dont_narrate_section():
    """The system prompt must explicitly tell the model to call tools
    instead of describing what it would do — the v1.1.15 failure mode."""
    assert "Act, don't narrate" in DEFAULT_SYSTEM_PROMPT or \
        "act, don't narrate" in DEFAULT_SYSTEM_PROMPT.lower()
    assert "without calling write_file is a failure" in DEFAULT_SYSTEM_PROMPT.lower()


# ─── Executor system prompt is directive ─────────────────────────────────────

def test_executor_prompt_is_directive():
    """The dual-mode executor must be told explicitly to USE tools, not
    paraphrase the plan."""
    full = _EXECUTOR_SYSTEM_PROMPT_PREFIX + "<plan>" + _EXECUTOR_SYSTEM_PROMPT_SUFFIX
    assert "act, do not narrate" in full.lower() or "ACT, DO NOT NARRATE" in full
    assert "write_file" in full
    assert "run_bash" in full
    assert "<coder_plan>" in full


def test_executor_prompt_wraps_coder_output_in_tags():
    """Suffix closes the <coder_plan> tag opened by prefix."""
    assert "<coder_plan>" in _EXECUTOR_SYSTEM_PROMPT_PREFIX
    assert "</coder_plan>" in _EXECUTOR_SYSTEM_PROMPT_SUFFIX


# ─── _strip_thinking_tags ────────────────────────────────────────────────────

def test_strip_well_formed_think_block():
    text = (
        "<think>The user wants a flask app. I'll create app.py first.</think>\n\n"
        "Here's what I did: ..."
    )
    out = _strip_thinking_tags(text)
    assert "<think>" not in out
    assert "</think>" not in out
    assert "Here's what I did" in out


def test_strip_orphan_closing_tag():
    """The exact pattern from the v1.1.15 user trace: bare `</think>` at
    the start of the reply because the opening tag was emitted in a
    thinking-channel that the API didn't surface."""
    text = (
        "I will write each file as specified.</think> I'll create the "
        "requirements.txt file with the required packages..."
    )
    out = _strip_thinking_tags(text)
    assert "</think>" not in out
    assert "I will write each file" in out
    assert "requirements.txt" in out


def test_strip_handles_thinking_thought_reasoning_aliases():
    text = (
        "<thinking>plan</thinking><thought>more plan</thought>"
        "<reasoning>even more</reasoning>The answer is 42."
    )
    out = _strip_thinking_tags(text)
    assert out == "The answer is 42."


def test_strip_preserves_legitimate_text():
    text = "Just a normal reply. No think tags here."
    assert _strip_thinking_tags(text) == text


def test_strip_returns_original_if_stripping_wipes_everything():
    """Don't ever return an empty string — the user always wants to see
    *something*. If the only content was inside think tags, keep the
    raw text instead."""
    text = "<think>everything was thinking</think>"
    assert _strip_thinking_tags(text) == text


def test_strip_handles_empty_input():
    assert _strip_thinking_tags("") == ""
    assert _strip_thinking_tags(None) is None


# ─── 429 retry with backoff ─────────────────────────────────────────────────

def _ok_response(text: str = "ok"):
    r = MagicMock()
    r.status_code = 200
    r.headers = {}
    r.json.return_value = {
        "choices": [{
            "message": {"content": text, "tool_calls": []},
            "finish_reason": "stop",
        }],
    }
    return r


def _rate_limited_response(retry_after: str = ""):
    r = MagicMock()
    r.status_code = 429
    r.headers = {"Retry-After": retry_after} if retry_after else {}
    r.text = '{"status":429,"title":"Too Many Requests"}'
    return r


def test_429_retries_once_then_succeeds(monkeypatch):
    """First call → 429, second call → 200. Provider returns the 200's body."""
    monkeypatch.setattr(time, "sleep", lambda s: None)  # no real backoff in tests
    client = MagicMock()
    client.post = MagicMock(side_effect=[_rate_limited_response(), _ok_response("hi")])
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
        client=client, timeout_s=10,
    )
    notes: list[str] = []
    p.set_tools_warning_sink(notes.append)
    response = p.complete([ProviderMessage(role="user", content="hi")], tools=[])
    assert response.text == "hi"
    assert client.post.call_count == 2
    assert any("rate-limited" in n.lower() for n in notes)


def test_429_after_retry_raises_actionable_error(monkeypatch):
    """Two 429s in a row → user-facing message that names the model and
    suggests switching, rather than dumping the JSON body."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    client = MagicMock()
    client.post = MagicMock(side_effect=[_rate_limited_response(), _rate_limited_response()])
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="moonshotai/kimi-k2.6",
        client=client, timeout_s=10,
    )
    with pytest.raises(PhantomError) as exc:
        p.complete([ProviderMessage(role="user", content="hi")], tools=[])
    msg = str(exc.value)
    assert "rate-limited" in msg.lower() or "429" in msg
    assert "kimi-k2.6" in msg
    assert "/model" in msg or "switch" in msg


def test_429_honours_retry_after_header(monkeypatch):
    """If the server sends Retry-After: 3, we sleep exactly that (capped)."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    client = MagicMock()
    client.post = MagicMock(side_effect=[_rate_limited_response("3"), _ok_response()])
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
        client=client, timeout_s=10,
    )
    p.complete([ProviderMessage(role="user", content="hi")], tools=[])
    assert len(sleeps) == 1
    assert sleeps[0] == 3.0


def test_non_429_error_does_not_retry(monkeypatch):
    """A 500 should surface immediately; only 429 triggers the retry path."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    bad = MagicMock(); bad.status_code = 500; bad.text = "internal error"; bad.headers = {}
    client = MagicMock()
    client.post = MagicMock(side_effect=[bad])
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
        client=client, timeout_s=10,
    )
    with pytest.raises(PhantomError, match="returned 500"):
        p.complete([ProviderMessage(role="user", content="hi")], tools=[])
    assert client.post.call_count == 1
    assert sleeps == []
