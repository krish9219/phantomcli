"""Tests for :mod:`phantom.agent.provider`.

OpenAICompatibleProvider tested via respx (httpx mock) so the call
shape against a real OpenAI-compatible API is verified without hitting
the network.
"""

from __future__ import annotations

import json

import pytest

respx = pytest.importorskip("respx")
import httpx  # noqa: E402

from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
    ProviderResponse,
    ScriptedProvider,
    ToolCall,
)
from phantom.errors import PhantomError


# ─── ScriptedProvider ────────────────────────────────────────────────────────


class TestScriptedProvider:
    def test_pops_in_order(self):
        p = ScriptedProvider.from_responses([
            ProviderResponse(text="one"),
            ProviderResponse(text="two"),
        ])
        msgs: list = []
        assert p.complete(msgs, tools=[]).text == "one"
        assert p.complete(msgs, tools=[]).text == "two"

    def test_exhausted_raises(self):
        p = ScriptedProvider.from_responses([])
        with pytest.raises(PhantomError, match="exhausted"):
            p.complete([], tools=[])

    def test_records_received(self):
        p = ScriptedProvider.from_responses([ProviderResponse(text="x")])
        p.complete([ProviderMessage(role="user", content="hi")], tools=[])
        assert len(p.received) == 1
        assert p.received[0][0].content == "hi"


# ─── OpenAICompatibleProvider ────────────────────────────────────────────────


@pytest.fixture
def provider_with_mock():
    transport = httpx.MockTransport(_dispatch)
    client = httpx.Client(transport=transport, base_url="https://api.example.com")
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="gpt-test",
        client=client,
    )
    yield p
    client.close()


_recorded_requests: list[httpx.Request] = []


def _dispatch(request: httpx.Request) -> httpx.Response:
    _recorded_requests.append(request)
    body = json.loads(request.content)
    # Branch on message content for deterministic responses.
    last = body["messages"][-1]["content"] if body.get("messages") else ""
    if "tool" in last:
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"x": 1}'},
                    }],
                },
            }],
        })
    if "boom" in last:
        return httpx.Response(500, text='{"error": "internal"}')
    return httpx.Response(200, json={
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": "hello back"},
        }],
    })


class TestOpenAICompatProvider:
    def setup_method(self):
        _recorded_requests.clear()

    def test_text_response(self, provider_with_mock):
        out = provider_with_mock.complete(
            [ProviderMessage(role="user", content="hi")],
            tools=[],
        )
        assert out.text == "hello back"
        assert out.finish_reason == "stop"
        assert not out.wants_tools

    def test_tool_call_parsed(self, provider_with_mock):
        out = provider_with_mock.complete(
            [ProviderMessage(role="user", content="please use a tool")],
            tools=[{"type": "function",
                    "function": {"name": "echo", "parameters": {}}}],
        )
        assert out.wants_tools
        assert out.tool_calls[0].name == "echo"
        assert out.tool_calls[0].arguments == {"x": 1}

    def test_request_payload_shape(self, provider_with_mock):
        provider_with_mock.complete(
            [ProviderMessage(role="user", content="hi")],
            tools=[],
        )
        req = _recorded_requests[-1]
        body = json.loads(req.content)
        assert body["model"] == "gpt-test"
        assert body["messages"][0]["content"] == "hi"
        # Authorization header is set.
        assert req.headers.get("authorization") == "Bearer sk-test"

    def test_tool_message_carries_tool_call_id(self, provider_with_mock):
        provider_with_mock.complete([
            ProviderMessage(role="user", content="hi"),
            ProviderMessage(role="tool",
                            content='{"ok":true}',
                            tool_call_id="tc1",
                            name="echo"),
        ], tools=[])
        req = _recorded_requests[-1]
        body = json.loads(req.content)
        tool_msg = body["messages"][-1]
        assert tool_msg["tool_call_id"] == "tc1"
        assert tool_msg["name"] == "echo"

    def test_500_wrapped_in_phantom_error(self, provider_with_mock):
        with pytest.raises(PhantomError, match="500"):
            provider_with_mock.complete(
                [ProviderMessage(role="user", content="boom")],
                tools=[],
            )

    def test_missing_base_url_rejected(self):
        with pytest.raises(PhantomError, match="base_url"):
            OpenAICompatibleProvider(base_url="", api_key="x", model="x")

    def test_missing_model_rejected(self):
        with pytest.raises(PhantomError, match="model"):
            OpenAICompatibleProvider(
                base_url="https://api.example.com", api_key="x", model="",
            )

    def test_tool_message_without_tool_call_id_rejected(self, provider_with_mock):
        with pytest.raises(PhantomError, match="tool_call_id"):
            provider_with_mock.complete([
                ProviderMessage(role="tool", content="x"),
            ], tools=[])
