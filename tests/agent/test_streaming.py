"""Tests for :mod:`phantom.agent.streaming`."""

from __future__ import annotations

import json

import httpx
import pytest

from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
)
from phantom.agent.streaming import (
    StreamChunk,
    ToolCallDelta,
    _iter_sse_events,
    _parse_chunk,
    drain_stream,
    stream,
)
from phantom.errors import PhantomError


# ─── SSE parser ──────────────────────────────────────────────────────────────


class TestSseParser:
    def test_yields_each_event(self):
        lines = [
            'data: {"a":1}', '',
            'data: {"b":2}', '',
            'data: [DONE]', '',
        ]
        events = list(_iter_sse_events(iter(lines)))
        assert events == ['{"a":1}', '{"b":2}', '[DONE]']

    def test_handles_bytes_input(self):
        lines = [b'data: hello', b'']
        events = list(_iter_sse_events(iter(lines)))
        assert events == ['hello']

    def test_skips_comments(self):
        lines = [':keepalive', '', 'data: real', '']
        events = list(_iter_sse_events(iter(lines)))
        assert events == ['real']

    def test_multi_data_line_event_joined(self):
        lines = ['data: line1', 'data: line2', '']
        events = list(_iter_sse_events(iter(lines)))
        assert events == ['line1\nline2']

    def test_no_terminator_still_yields(self):
        # Some servers close without a final blank line.
        lines = ['data: payload']
        events = list(_iter_sse_events(iter(lines)))
        assert events == ['payload']


# ─── chunk parser ────────────────────────────────────────────────────────────


class TestParseChunk:
    def test_done_sentinel(self):
        c = _parse_chunk("[DONE]")
        assert c.done is True

    def test_text_delta(self):
        c = _parse_chunk(json.dumps({
            "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}],
        }))
        assert c.text == "Hello"
        assert c.tool_call is None
        assert c.done is False

    def test_tool_call_delta(self):
        c = _parse_chunk(json.dumps({
            "choices": [{
                "delta": {"tool_calls": [{
                    "index": 0, "id": "call_1", "type": "function",
                    "function": {"name": "fn", "arguments": '{"x":'},
                }]},
                "finish_reason": None,
            }],
        }))
        assert c.tool_call is not None
        assert c.tool_call.id == "call_1"
        assert c.tool_call.name == "fn"
        assert c.tool_call.arguments_raw == '{"x":'

    def test_malformed_json_returns_empty(self):
        c = _parse_chunk("{not json")
        assert c.text == "" and c.tool_call is None and c.done is False

    def test_finish_reason_propagates(self):
        c = _parse_chunk(json.dumps({
            "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}],
        }))
        assert c.finish_reason == "stop"


# ─── ToolCallDelta ───────────────────────────────────────────────────────────


class TestToolCallDelta:
    def test_merge_progressively(self):
        a = ToolCallDelta(id="x", name="fn", arguments_raw='{"x":')
        b = ToolCallDelta(arguments_raw='1}')
        a.merge(b)
        assert a.id == "x" and a.name == "fn"
        assert a.arguments_raw == '{"x":1}'

    def test_finalize_parses_args(self):
        d = ToolCallDelta(id="x", name="fn", arguments_raw='{"a":1,"b":2}')
        tc = d.finalize()
        assert tc.id == "x" and tc.name == "fn"
        assert tc.arguments == {"a": 1, "b": 2}

    def test_finalize_with_invalid_args_returns_empty_dict(self):
        d = ToolCallDelta(id="x", name="fn", arguments_raw='{not')
        assert d.finalize().arguments == {}

    def test_finalize_with_empty_args(self):
        d = ToolCallDelta(id="x", name="fn", arguments_raw="")
        assert d.finalize().arguments == {}


# ─── stream() against a mocked SSE endpoint ─────────────────────────────────


def _sse(events: list[dict | str]) -> str:
    """Render *events* as an SSE byte stream body."""
    out: list[str] = []
    for evt in events:
        body = evt if isinstance(evt, str) else json.dumps(evt)
        out.append(f"data: {body}\n\n")
    return "".join(out)


def _make_provider(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="gpt-test",
        client=client,
    )


class TestStreamE2E:
    def test_text_only_stream(self):
        body = _sse([
            {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            "[DONE]",
        ])
        def handler(req):
            return httpx.Response(200, text=body,
                                  headers={"content-type": "text/event-stream"})
        p = _make_provider(handler)
        chunks = list(stream(p, [ProviderMessage(role="user", content="hi")]))
        # The final chunk must be done=True.
        assert chunks[-1].done is True
        # Drain reduces to a complete response.
        response = drain_stream(iter(chunks))
        assert response.text == "Hello"
        assert response.finish_reason == "stop"
        assert not response.tool_calls

    def test_tool_call_stream_assembles(self):
        body = _sse([
            {"choices": [{"delta": {
                "tool_calls": [{"index": 0, "id": "call_1",
                                "function": {"name": "echo", "arguments": '{"text":"'}}],
            }, "finish_reason": None}]},
            {"choices": [{"delta": {
                "tool_calls": [{"index": 0,
                                "function": {"arguments": 'hi"}'}}],
            }, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            "[DONE]",
        ])
        def handler(req):
            return httpx.Response(200, text=body,
                                  headers={"content-type": "text/event-stream"})
        p = _make_provider(handler)
        response = drain_stream(stream(
            p, [ProviderMessage(role="user", content="x")],
            tools=[{"type": "function",
                    "function": {"name": "echo", "parameters": {}}}],
        ))
        assert response.tool_calls
        tc = response.tool_calls[0]
        assert tc.name == "echo"
        assert tc.arguments == {"text": "hi"}

    def test_500_response_raises(self):
        def handler(req):
            return httpx.Response(500, text="bad")
        p = _make_provider(handler)
        with pytest.raises(PhantomError, match="500"):
            list(stream(p, [ProviderMessage(role="user", content="x")]))

    def test_request_carries_stream_true(self):
        captured: list = []
        def handler(req):
            captured.append(json.loads(req.content))
            return httpx.Response(200, text=_sse(["[DONE]"]),
                                  headers={"content-type": "text/event-stream"})
        p = _make_provider(handler)
        list(stream(p, [ProviderMessage(role="user", content="x")]))
        assert captured[0]["stream"] is True
        assert captured[0]["model"] == "gpt-test"

    def test_tools_payload_carried(self):
        captured: list = []
        def handler(req):
            captured.append(json.loads(req.content))
            return httpx.Response(200, text=_sse(["[DONE]"]),
                                  headers={"content-type": "text/event-stream"})
        p = _make_provider(handler)
        tools = [{"type": "function",
                  "function": {"name": "x", "parameters": {}}}]
        list(stream(p, [ProviderMessage(role="user", content="x")], tools=tools))
        assert captured[0]["tools"] == tools
        assert captured[0]["tool_choice"] == "auto"

    def test_no_done_sentinel_synthesises_done(self):
        body = _sse([
            {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]},
        ])
        def handler(req):
            return httpx.Response(200, text=body,
                                  headers={"content-type": "text/event-stream"})
        p = _make_provider(handler)
        chunks = list(stream(p, [ProviderMessage(role="user", content="x")]))
        # Last chunk must signal done even though server didn't say [DONE].
        assert chunks[-1].done is True
