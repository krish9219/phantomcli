"""Tests for the OpenAI-compatible SSE stream parser."""
from __future__ import annotations

import pytest

from omnicli.openai_stream import parse_stream, parse_bytes


# ─── Fixtures (hand-authored SSE chunks mirroring real OpenAI payloads) ──────

TEXT_STREAM = """\
data: {"id":"x","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"x","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hello, "},"finish_reason":null}]}

data: {"id":"x","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"content":"world!"},"finish_reason":null}]}

data: {"id":"x","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":5,"total_tokens":17}}

data: [DONE]
"""

TOOL_CALL_STREAM = """\
data: {"id":"x","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"run_bash","arguments":""}}]},"finish_reason":null}]}

data: {"id":"x","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"com"}}]},"finish_reason":null}]}

data: {"id":"x","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"mand\\":\\"ls"}}]},"finish_reason":null}]}

data: {"id":"x","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"}"}}]},"finish_reason":null}]}

data: {"id":"x","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
"""

MULTI_TOOL_STREAM = """\
data: {"id":"x","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"c1","function":{"name":"run_bash","arguments":"{\\"command\\":\\"ls\\"}"}}]},"finish_reason":null}]}

data: {"id":"x","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"c2","function":{"name":"read_file","arguments":"{\\"path\\":\\"/tmp\\"}"}}]},"finish_reason":null}]}

data: {"id":"x","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
"""


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestTextStream:
    def test_text_reassembled(self):
        r = parse_stream(TEXT_STREAM.splitlines())
        assert r.text == "Hello, world!"

    def test_finish_reason_captured(self):
        r = parse_stream(TEXT_STREAM.splitlines())
        assert r.finish_reason == "stop"

    def test_model_captured(self):
        r = parse_stream(TEXT_STREAM.splitlines())
        assert r.model == "gpt-4o"

    def test_usage_captured(self):
        r = parse_stream(TEXT_STREAM.splitlines())
        assert r.usage["prompt_tokens"]     == 12
        assert r.usage["completion_tokens"] == 5
        assert r.usage["total_tokens"]      == 17


class TestToolCallStream:
    def test_single_tool_call_reassembled(self):
        r = parse_stream(TOOL_CALL_STREAM.splitlines())
        assert len(r.tool_calls) == 1
        tc = r.tool_calls[0]
        assert tc["name"] == "run_bash"
        assert tc["args"] == {"command": "ls"}
        assert tc["id"] == "call_1"

    def test_finish_reason_tool_calls(self):
        r = parse_stream(TOOL_CALL_STREAM.splitlines())
        assert r.finish_reason == "tool_calls"


class TestMultipleToolCalls:
    def test_two_tools_both_captured(self):
        r = parse_stream(MULTI_TOOL_STREAM.splitlines())
        assert len(r.tool_calls) == 2
        names = [t["name"] for t in r.tool_calls]
        assert names == ["run_bash", "read_file"]
        assert r.tool_calls[0]["args"] == {"command": "ls"}
        assert r.tool_calls[1]["args"] == {"path": "/tmp"}


class TestDoneTerminator:
    def test_done_ends_stream(self):
        lines = TEXT_STREAM.splitlines() + [
            # Any frame AFTER [DONE] must be ignored
            "",
            "data: {\"choices\":[{\"delta\":{\"content\":\"SHOULD NOT APPEAR\"}}]}",
            "",
        ]
        r = parse_stream(lines)
        assert "SHOULD NOT APPEAR" not in r.text


class TestMalformedChunks:
    def test_non_json_frame_skipped(self):
        lines = [
            "data: not json",
            "",
            "data: {\"choices\":[{\"delta\":{\"content\":\"OK\"}}]}",
            "",
            "data: [DONE]",
            "",
        ]
        r = parse_stream(lines)
        assert r.text == "OK"

    def test_empty_choices_safe(self):
        lines = [
            "data: {\"choices\":[]}",
            "",
            "data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}",
            "",
            "data: [DONE]",
            "",
        ]
        r = parse_stream(lines)
        assert r.text == "hi"


class TestParseBytes:
    def test_raw_bytes_decoded(self):
        r = parse_bytes(TEXT_STREAM.encode("utf-8"))
        assert r.text == "Hello, world!"

    def test_invalid_utf8_does_not_crash(self):
        blob = TEXT_STREAM.encode("utf-8") + b"\xff\xfe"
        r = parse_bytes(blob)
        assert "Hello, world!" in r.text


class TestEmptyStream:
    def test_empty_returns_empty(self):
        r = parse_stream([])
        assert r.text == ""
        assert r.tool_calls == []
        assert r.finish_reason == ""


class TestStreamingBoundarySafety:
    def test_tool_args_split_across_4_chunks(self):
        """Args splintered into 4 pieces should reassemble cleanly."""
        r = parse_stream(TOOL_CALL_STREAM.splitlines())
        assert r.tool_calls[0]["args"] == {"command": "ls"}

    def test_text_across_many_chunks(self):
        """Even extreme chunking produces correct concat."""
        chars = "The quick brown fox"
        frames = []
        for ch in chars:
            frames.append(
                f'data: {{"choices":[{{"delta":{{"content":"{ch}"}}}}]}}'
            )
            frames.append("")
        frames.append("data: [DONE]")
        frames.append("")
        r = parse_stream(frames)
        assert r.text == chars
