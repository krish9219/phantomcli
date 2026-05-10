"""Tests for v1.1.27 — streaming responses + interactive /confirm gate."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from phantom.agent import AgentSession, ScriptedProvider, ToolDefinition
from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
    ProviderResponse,
    ToolCall,
)
from phantom.profile import Profile, load_profile, save_profile


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path


# ─── Streaming: SSE parser dispatches chunks ────────────────────────────────

def _sse_response(events: list[str], status: int = 200):
    """Build a fake httpx.Response that yields *events* over iter_lines."""
    fake = MagicMock()
    fake.status_code = status
    fake.headers = {}
    fake.iter_lines.return_value = iter(events)
    fake.iter_bytes.return_value = iter([b""])

    class _CM:
        def __enter__(self_inner): return fake
        def __exit__(self_inner, *a): return False
    return _CM()


def test_stream_dispatches_text_chunks_in_order():
    chunks_received: list[str] = []
    events = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":", "}}]}',
        'data: {"choices":[{"delta":{"content":"world"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        'data: [DONE]',
    ]
    fake_client = MagicMock()
    fake_client.stream.return_value = _sse_response(events)
    provider = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=fake_client,
    )
    response = provider.complete(
        [ProviderMessage(role="user", content="hi")],
        tools=[],
        on_chunk=chunks_received.append,
    )
    assert chunks_received == ["Hello", ", ", "world"]
    assert response.text == "Hello, world"
    assert response.finish_reason == "stop"


def test_stream_accumulates_tool_calls_across_chunks():
    """tool_calls arrive split across SSE deltas: id+name first, then
    arguments string fragments. Provider must accumulate by index."""
    events = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_x",'
        '"function":{"name":"run_bash"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"{\\"command\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"\\"echo hi\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        'data: [DONE]',
    ]
    fake_client = MagicMock()
    fake_client.stream.return_value = _sse_response(events)
    provider = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=fake_client,
    )
    response = provider.complete(
        [ProviderMessage(role="user", content="run something")],
        tools=[{"type": "function", "function": {"name": "run_bash"}}],
        on_chunk=lambda c: None,
    )
    assert len(response.tool_calls) == 1
    tc = response.tool_calls[0]
    assert tc.id == "call_x"
    assert tc.name == "run_bash"
    assert tc.arguments == {"command": "echo hi"}


def test_stream_skips_empty_lines_and_done_sentinel():
    events = [
        '',
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        '',
        'data: [DONE]',
        # Anything after [DONE] should be ignored.
        'data: {"choices":[{"delta":{"content":"ghost"}}]}',
    ]
    fake_client = MagicMock()
    fake_client.stream.return_value = _sse_response(events)
    provider = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=fake_client,
    )
    chunks: list[str] = []
    response = provider.complete(
        [ProviderMessage(role="user", content="x")], tools=[],
        on_chunk=chunks.append,
    )
    assert chunks == ["hi"]
    assert response.text == "hi"


def test_stream_ignores_malformed_sse_lines():
    events = [
        'data: {"choices":[{"delta":{"content":"a"}}]}',
        'data: {malformed json',
        'data: not-data-prefixed-line',  # gets filtered (no data: prefix? well actually it has data:)
        'comment: # this is a comment',  # no data: prefix → skipped
        'data: {"choices":[{"delta":{"content":"b"}}]}',
        'data: [DONE]',
    ]
    fake_client = MagicMock()
    fake_client.stream.return_value = _sse_response(events)
    provider = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=fake_client,
    )
    chunks: list[str] = []
    response = provider.complete(
        [ProviderMessage(role="user", content="x")], tools=[],
        on_chunk=chunks.append,
    )
    assert chunks == ["a", "b"]


def test_stream_callback_exception_does_not_kill_stream():
    """If the chat REPL's printer crashes (e.g. broken stdout), the
    provider should keep streaming and finish cleanly."""
    events = [
        'data: {"choices":[{"delta":{"content":"a"}}]}',
        'data: {"choices":[{"delta":{"content":"b"}}]}',
        'data: [DONE]',
    ]
    fake_client = MagicMock()
    fake_client.stream.return_value = _sse_response(events)
    provider = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=fake_client,
    )
    def bad(_chunk):
        raise RuntimeError("printer crashed")
    response = provider.complete(
        [ProviderMessage(role="user", content="x")], tools=[],
        on_chunk=bad,
    )
    assert response.text == "ab"


def test_complete_without_on_chunk_uses_non_streaming_path():
    """When the caller doesn't pass on_chunk, the provider should NOT
    set stream=True — preserves backwards compatibility for callers
    that don't want streaming."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {}
    fake_response.json.return_value = {
        "choices": [{
            "message": {"content": "ok", "tool_calls": []},
            "finish_reason": "stop",
        }],
    }
    fake_client = MagicMock()
    fake_client.post.return_value = fake_response
    provider = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=fake_client,
    )
    response = provider.complete(
        [ProviderMessage(role="user", content="x")], tools=[],
    )
    assert response.text == "ok"
    fake_client.post.assert_called_once()
    payload = fake_client.post.call_args.kwargs["json"]
    assert "stream" not in payload  # stream=False omitted when not requested


# ─── Approval gate (on_tool_call_approve) ───────────────────────────────────

def _identity_tool() -> ToolDefinition:
    return ToolDefinition(
        name="echo",
        description="echo args",
        input_schema={"type": "object"},
        handler=lambda args: json.dumps(args),
    )


def test_approval_hook_proceeds_when_returns_true():
    captured = []
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            ProviderResponse(
                text="",
                tool_calls=(ToolCall(id="t1", name="echo", arguments={"x": 1}),),
                finish_reason="tool_calls",
            ),
            ProviderResponse(text="done", tool_calls=(), finish_reason="stop"),
        ]),
        tools=[_identity_tool()],
    )
    session.on_tool_call_approve = lambda r, tc: True
    session.on_tool_result = lambda r, tc, res: captured.append(res)
    out = session.respond_to("run echo")
    assert out == "done"
    # Tool actually ran — handler returned the args JSON.
    assert any('"x": 1' in c or '"x":1' in c for c in captured)


def test_approval_hook_declined_returns_user_declined_marker():
    captured = []
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            ProviderResponse(
                text="",
                tool_calls=(ToolCall(id="t1", name="echo", arguments={"x": 1}),),
                finish_reason="tool_calls",
            ),
            ProviderResponse(text="ok, skipped", tool_calls=(), finish_reason="stop"),
        ]),
        tools=[_identity_tool()],
    )
    session.on_tool_call_approve = lambda r, tc: False
    session.on_tool_result = lambda r, tc, res: captured.append(res)
    out = session.respond_to("run echo")
    assert out == "ok, skipped"
    # The handler was NOT called (echo wasn't run); the tool result is
    # the "user declined" JSON marker.
    assert len(captured) == 1
    parsed = json.loads(captured[0])
    assert "declined" in parsed["error"].lower()
    assert "hint" in parsed


def test_approval_hook_exception_does_not_block_tool():
    """A buggy approval hook must not break the agent — fail open."""
    captured = []
    def bad_hook(_r, _tc):
        raise RuntimeError("hook bug")
    session = AgentSession(
        provider=ScriptedProvider.from_responses([
            ProviderResponse(
                text="",
                tool_calls=(ToolCall(id="t1", name="echo", arguments={"x": 1}),),
                finish_reason="tool_calls",
            ),
            ProviderResponse(text="done", tool_calls=(), finish_reason="stop"),
        ]),
        tools=[_identity_tool()],
    )
    session.on_tool_call_approve = bad_hook
    session.on_tool_result = lambda r, tc, res: captured.append(res)
    out = session.respond_to("run")
    assert out == "done"
    # Tool ran despite the broken hook (fail-open).
    assert any('"x": 1' in c or '"x":1' in c for c in captured)


# ─── /confirm slash + profile field ─────────────────────────────────────────

def test_profile_persists_confirm_destructive(home: Path):
    p = Profile(user_name="A", assistant_name="P", workspace_path="/x",
                confirm_destructive=True)
    save_profile(p)
    again = load_profile()
    assert again.confirm_destructive is True


def test_slash_confirm_on_off(home: Path):
    save_profile(Profile(user_name="A", assistant_name="P", workspace_path="/x"))
    from phantom.agent import ScriptedProvider
    from phantom.cli.chat import _handle_slash
    session = AgentSession(provider=ScriptedProvider(), tools=[])
    out = []
    _handle_slash(session=session, head="/confirm", arg="on", write=out.append)
    assert load_profile().confirm_destructive is True
    _handle_slash(session=session, head="/confirm", arg="off", write=out.append)
    assert load_profile().confirm_destructive is False


def test_slash_confirm_no_arg_reports_status(home: Path):
    save_profile(Profile(user_name="A", assistant_name="P", workspace_path="/x",
                         confirm_destructive=True))
    from phantom.agent import ScriptedProvider
    from phantom.cli.chat import _handle_slash
    session = AgentSession(provider=ScriptedProvider(), tools=[])
    out = []
    _handle_slash(session=session, head="/confirm", arg="", write=out.append)
    text = "".join(out)
    assert "on" in text.lower()
    assert "/confirm" in text


def test_help_lists_confirm(home: Path):
    save_profile(Profile())
    from phantom.agent import ScriptedProvider
    from phantom.cli.chat import _handle_slash
    session = AgentSession(provider=ScriptedProvider(), tools=[])
    out = []
    _handle_slash(session=session, head="/help", arg="", write=out.append)
    assert "/confirm" in "".join(out)
