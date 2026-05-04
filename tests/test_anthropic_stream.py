"""Tests for anthropic_stream — SSE parsing + StreamAssembler integration
driven by captured fixture payloads."""
from __future__ import annotations

import pytest

from omnicli.anthropic_stream import (
    iter_sse_frames, parse_stream, parse_bytes,
)


# ─── Tiny fixtures (realistic SSE, hand-authored) ────────────────────────────

TEXT_ONLY = """\
event: message_start
data: {"type":"message_start","message":{"id":"m1","usage":{"input_tokens":12,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello, "}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"world!"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":8}}

event: message_stop
data: {"type":"message_stop"}
"""

TOOL_USE = """\
event: message_start
data: {"type":"message_start","message":{"id":"m2","usage":{"input_tokens":30,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"run_bash","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"com"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"mand\\":\\"ls"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":" -la\\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":18}}

event: message_stop
data: {"type":"message_stop"}
"""

MIXED = """\
event: message_start
data: {"type":"message_start","message":{"id":"m3","usage":{"input_tokens":50,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Let me check."}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_A","name":"read_file","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":\\"/tmp/x.txt\\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}

event: message_stop
data: {"type":"message_stop"}
"""


class TestSseFrameIter:
    def test_parses_frame_with_event_and_data(self):
        frames = list(iter_sse_frames([
            "event: hello",
            "data: {\"k\": 1}",
            "",
        ]))
        assert len(frames) == 1
        assert frames[0].event == "hello"
        assert frames[0].data  == '{"k": 1}'

    def test_ignores_comments(self):
        frames = list(iter_sse_frames([
            ": this is a comment",
            "event: real",
            "data: 42",
            "",
        ]))
        assert len(frames) == 1
        assert frames[0].event == "real"

    def test_multiple_data_lines_joined(self):
        frames = list(iter_sse_frames([
            "event: ev",
            "data: first",
            "data: second",
            "",
        ]))
        assert frames[0].data == "first\nsecond"

    def test_missing_trailing_blank_still_yields(self):
        frames = list(iter_sse_frames([
            "event: ev",
            "data: 1",
        ]))
        assert len(frames) == 1


class TestTextOnlyParse:
    def test_text_reassembled(self):
        r = parse_stream(TEXT_ONLY.splitlines())
        assert r.text == "Hello, world!"

    def test_tool_calls_empty(self):
        r = parse_stream(TEXT_ONLY.splitlines())
        assert r.tool_calls == []

    def test_stop_reason(self):
        r = parse_stream(TEXT_ONLY.splitlines())
        assert r.stop_reason == "end_turn"

    def test_usage_totalled(self):
        r = parse_stream(TEXT_ONLY.splitlines())
        assert r.usage.get("prompt_tokens")     == 12
        assert r.usage.get("completion_tokens") == 8


class TestToolUseParse:
    def test_args_reassembled_across_deltas(self):
        r = parse_stream(TOOL_USE.splitlines())
        assert len(r.tool_calls) == 1
        tc = r.tool_calls[0]
        assert tc["id"] == "toolu_1"
        assert tc["name"] == "run_bash"
        assert tc["args"] == {"command": "ls -la"}

    def test_stop_reason_tool_use(self):
        r = parse_stream(TOOL_USE.splitlines())
        assert r.stop_reason == "tool_use"


class TestMixedContentParse:
    def test_text_and_tool_both_captured(self):
        r = parse_stream(MIXED.splitlines())
        assert r.text == "Let me check."
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0]["name"] == "read_file"
        assert r.tool_calls[0]["args"] == {"path": "/tmp/x.txt"}

    def test_indexes_preserved(self):
        r = parse_stream(MIXED.splitlines())
        # Only one tool call, but its index in the stream was 1
        assert r.tool_calls[0]["id"] == "toolu_A"


class TestParseBytes:
    def test_parse_from_raw_bytes(self):
        out = parse_bytes(TEXT_ONLY.encode("utf-8"))
        assert out.text == "Hello, world!"

    def test_parse_invalid_utf8_replaced(self):
        # Force an invalid byte mid-stream; parser shouldn't crash
        blob = TEXT_ONLY.encode("utf-8") + b"\xff\xff"
        out = parse_bytes(blob)
        assert "Hello, world!" in out.text


class TestMalformedFrames:
    def test_non_json_data_skipped(self):
        lines = [
            "event: content_block_delta",
            "data: not json",
            "",
            "event: content_block_delta",
            "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"OK\"}}",
            "",
        ]
        r = parse_stream(lines)
        assert r.text == "OK"

    def test_unknown_event_ignored(self):
        lines = [
            "event: some_future_event",
            "data: {\"foo\":1}",
            "",
            "event: content_block_delta",
            "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"hi\"}}",
            "",
        ]
        assert parse_stream(lines).text == "hi"


class TestMultipleUsageUpdates:
    def test_input_and_output_tokens_merged(self):
        # message_start has input + output=0, message_delta adds completion
        r = parse_stream(TEXT_ONLY.splitlines())
        assert r.usage["prompt_tokens"]     == 12
        assert r.usage["completion_tokens"] == 8
