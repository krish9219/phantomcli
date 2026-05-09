"""Tests for the OpenAICompatibleProvider tools-fallback retry path."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
    _ToolsNotSupported,
    _looks_like_tool_rejection,
)
from phantom.errors import PhantomError


@pytest.mark.parametrize("body", [
    'Internal server error: Object of type Undefined is not JSON serializable',
    "this model does not support tool calls",
    "tools are not supported on this endpoint",
    "function calling is not supported",
])
def test_looks_like_tool_rejection_known_phrases(body: str):
    assert _looks_like_tool_rejection(body) is True


def test_looks_like_tool_rejection_unrelated_5xx():
    assert _looks_like_tool_rejection("upstream timed out") is False
    assert _looks_like_tool_rejection("rate limit exceeded") is False


def _stub_client(*responses):
    """Build a fake httpx.Client returning the given canned responses."""
    client = MagicMock()
    client.post = MagicMock(side_effect=list(responses))
    return client


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


def _error_response(status: int, body: str):
    r = MagicMock()
    r.status_code = status
    r.text = body
    return r


def _msgs():
    return [
        ProviderMessage(role="system", content="be brief"),
        ProviderMessage(role="user", content="hi"),
    ]


def _tools():
    return [{
        "type": "function",
        "function": {
            "name": "do_thing",
            "description": "x",
            "parameters": {"type": "object", "properties": {}},
        },
    }]


def test_first_call_with_tools_500_undefined_retries_without_tools():
    """The exact NVIDIA NIM minimax bug from the user's session."""
    bad = _error_response(
        500, '{"error":{"message":"Internal server error: Object of type Undefined is not JSON serializable"}}',
    )
    good = _ok_response("retry worked")
    client = _stub_client(bad, good)
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1",
        api_key="k",
        model="minimaxai/minimax-m2.5",
        client=client,
    )
    notes: list[str] = []
    p.set_tools_warning_sink(notes.append)

    response = p.complete(_msgs(), tools=_tools())

    assert response.text == "retry worked"
    assert client.post.call_count == 2
    # First call had tools, second didn't.
    first_payload = client.post.call_args_list[0].kwargs["json"]
    second_payload = client.post.call_args_list[1].kwargs["json"]
    assert "tools" in first_payload
    assert "tools" not in second_payload
    assert "tool_choice" not in second_payload
    # User got a notice.
    assert any("falling back" in n.lower() for n in notes)


def test_subsequent_calls_skip_tools_after_latch():
    """After the latch flips, even passing tools=[...] sends without them."""
    bad = _error_response(500, "Object of type Undefined is not JSON serializable")
    good1 = _ok_response("first")
    good2 = _ok_response("second")
    client = _stub_client(bad, good1, good2)
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=client,
    )

    p.complete(_msgs(), tools=_tools())
    p.complete(_msgs(), tools=_tools())

    assert client.post.call_count == 3  # 1 fail + 1 retry + 1 second turn
    third_payload = client.post.call_args_list[2].kwargs["json"]
    assert "tools" not in third_payload


def test_unrelated_5xx_raises_without_retry():
    """A 502 'upstream timed out' should NOT be misclassified as a tool issue."""
    bad = _error_response(502, "upstream timed out")
    client = _stub_client(bad)
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=client,
    )
    with pytest.raises(PhantomError, match="returned 502"):
        p.complete(_msgs(), tools=_tools())
    assert client.post.call_count == 1


def test_no_tools_payload_does_not_trigger_fallback():
    """If we never sent tools, a 500 must surface the original error verbatim."""
    bad = _error_response(500, "Object of type Undefined is not JSON serializable")
    client = _stub_client(bad)
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m", client=client,
    )
    with pytest.raises(PhantomError, match="returned 500"):
        p.complete(_msgs(), tools=[])
    assert client.post.call_count == 1


def test_tools_supported_false_at_init_skips_tools():
    """A caller that already knows tools are unsupported can opt out upfront."""
    good = _ok_response("ok")
    client = _stub_client(good)
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
        client=client, tools_supported=False,
    )
    p.complete(_msgs(), tools=_tools())
    payload = client.post.call_args.kwargs["json"]
    assert "tools" not in payload
