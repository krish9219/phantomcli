"""Tests for the streaming delta assembler.

Covers: text accumulation, UTF-8 chunk boundary safety, OpenAI-style
tool-call deltas with argument streaming, partial-JSON repair, multi-tool
indices, idempotent finalize."""
from __future__ import annotations

import pytest

from omnicli.stream_assembler import StreamAssembler, _repair_partial_json


class TestTextStream:
    def test_empty_stream(self):
        s = StreamAssembler()
        text, tools, warnings = s.finalize()
        assert text == ""
        assert tools == []

    def test_accumulates_text(self):
        s = StreamAssembler()
        s.push_text("Hello, ")
        s.push_text("world")
        s.push_text("!")
        text, _, _ = s.finalize()
        assert text == "Hello, world!"

    def test_utf8_split_across_chunks(self):
        """A 2-byte UTF-8 char split across chunk boundaries must not
        corrupt the output."""
        s = StreamAssembler()
        # "é" is 0xC3 0xA9 in UTF-8
        s.push_text(b"caf\xc3")   # first byte of é
        s.push_text(b"\xa9")      # second byte
        text, _, _ = s.finalize()
        assert text == "café"

    def test_multibyte_emoji_split(self):
        s = StreamAssembler()
        # "🔥" is F0 9F 94 A5 — split in the middle
        s.push_text(b"hi \xf0\x9f")
        s.push_text(b"\x94\xa5!")
        text, _, _ = s.finalize()
        assert text == "hi 🔥!"

    def test_invalid_utf8_replaced(self):
        """Trailing incomplete UTF-8 at finalize should produce replacement
        chars, not crash."""
        s = StreamAssembler()
        s.push_text(b"valid \xc3")  # dangling half-char
        text, _, warnings = s.finalize()
        assert "valid" in text


class TestToolCallSingleDelta:
    def test_single_complete_call(self):
        s = StreamAssembler()
        s.push_tool_delta(index=0, id_="call_1", name="run_bash",
                          args_chunk='{"command": "ls"}')
        _, tools, _ = s.finalize()
        assert len(tools) == 1
        assert tools[0]["id"] == "call_1"
        assert tools[0]["name"] == "run_bash"
        assert tools[0]["args"] == {"command": "ls"}

    def test_args_split_across_chunks(self):
        s = StreamAssembler()
        s.push_tool_delta(index=0, id_="c1", name="run_bash", args_chunk='{"com')
        s.push_tool_delta(index=0, args_chunk='mand":"')
        s.push_tool_delta(index=0, args_chunk='ls -la"}')
        _, tools, _ = s.finalize()
        assert tools[0]["args"] == {"command": "ls -la"}


class TestMultipleToolCalls:
    def test_two_tools_interleaved(self):
        s = StreamAssembler()
        s.push_tool_delta(index=0, name="run_bash", args_chunk='{"comm')
        s.push_tool_delta(index=1, name="write_file", args_chunk='{"pa')
        s.push_tool_delta(index=0, args_chunk='and":"ls"}')
        s.push_tool_delta(index=1, args_chunk='th":"/tmp/x","content":"hi"}')
        _, tools, _ = s.finalize()
        assert len(tools) == 2
        # Sorted by index
        assert tools[0]["name"] == "run_bash"
        assert tools[0]["args"] == {"command": "ls"}
        assert tools[1]["name"] == "write_file"
        assert tools[1]["args"] == {"path": "/tmp/x", "content": "hi"}

    def test_out_of_order_indices(self):
        s = StreamAssembler()
        s.push_tool_delta(index=2, name="c", args_chunk='{}')
        s.push_tool_delta(index=0, name="a", args_chunk='{}')
        s.push_tool_delta(index=1, name="b", args_chunk='{}')
        _, tools, _ = s.finalize()
        assert [t["name"] for t in tools] == ["a", "b", "c"]


class TestOpenAIDeltaShape:
    def test_content_delta(self):
        s = StreamAssembler()
        s.push_delta({"content": "hello"})
        s.push_delta({"content": " there"})
        text, _, _ = s.finalize()
        assert text == "hello there"

    def test_tool_call_delta_shape(self):
        s = StreamAssembler()
        s.push_delta({"tool_calls": [
            {"index": 0, "id": "c1",
             "function": {"name": "run_bash", "arguments": '{"co'}},
        ]})
        s.push_delta({"tool_calls": [
            {"index": 0, "function": {"arguments": 'mmand":"ls"}'}},
        ]})
        _, tools, _ = s.finalize()
        assert tools[0]["args"] == {"command": "ls"}

    def test_mixed_content_and_tool_calls(self):
        s = StreamAssembler()
        s.push_delta({"content": "Thinking..."})
        s.push_delta({"tool_calls": [
            {"index": 0, "function": {"name": "run_bash", "arguments": '{}'}},
        ]})
        text, tools, _ = s.finalize()
        assert text == "Thinking..."
        assert len(tools) == 1

    def test_list_shaped_content_parts(self):
        s = StreamAssembler()
        s.push_delta({"content": [
            {"type": "text", "text": "part1 "},
            {"type": "text", "text": "part2"},
        ]})
        text, _, _ = s.finalize()
        assert text == "part1 part2"

    def test_malformed_delta_is_ignored(self):
        s = StreamAssembler()
        s.push_delta("not a dict")  # type: ignore[arg-type]
        s.push_delta({})
        text, tools, _ = s.finalize()
        assert text == ""
        assert tools == []


class TestPartialJsonRepair:
    def test_unclosed_brace_repaired(self):
        out = _repair_partial_json('{"command": "ls"')
        assert out == {"command": "ls"}

    def test_unclosed_string_repaired(self):
        out = _repair_partial_json('{"command": "ls')
        assert out == {"command": "ls"}

    def test_unclosed_array_repaired(self):
        out = _repair_partial_json('{"tasks": ["a", "b"')
        assert out == {"tasks": ["a", "b"]}

    def test_trailing_comma_stripped(self):
        out = _repair_partial_json('{"a": 1, "b": 2,')
        assert out == {"a": 1, "b": 2}

    def test_dangling_key_with_no_value_dropped(self):
        out = _repair_partial_json('{"a": 1, "b": ')
        assert out == {"a": 1}

    def test_non_object_returns_none(self):
        assert _repair_partial_json('["just an array"') is None
        assert _repair_partial_json('"just a string"') is None

    def test_empty_returns_none(self):
        assert _repair_partial_json("") is None

    def test_assembler_uses_repair_on_truncated_args(self):
        s = StreamAssembler()
        s.push_tool_delta(index=0, name="run_bash", args_chunk='{"command": "ls')
        _, tools, warnings = s.finalize()
        # Args repaired → real dict, not None
        assert tools[0]["args"] == {"command": "ls"}
        assert any("repaired" in w for w in warnings)


class TestUnrepairableArgs:
    def test_nonsense_args_reported_in_pending(self):
        """Unrepairable JSON: args falls back to the raw string (for
        debuggability) but `pending` lists the index so the caller knows
        the parse failed."""
        s = StreamAssembler()
        s.push_tool_delta(index=0, name="x", args_chunk="{{{{not json")
        _, tools, warnings = s.finalize()
        # Fallback to raw string rather than dropping data silently
        assert tools[0]["args"] == "{{{{not json"
        assert any("unparseable" in w for w in warnings)
        assert s.pending == [0]


class TestLifecycle:
    def test_double_finalize_safe(self):
        s = StreamAssembler()
        s.push_text("a")
        t1, c1, _ = s.finalize()
        t2, c2, _ = s.finalize()
        assert t1 == t2 == "a"
        assert c1 == c2 == []

    def test_push_after_finalize_raises(self):
        s = StreamAssembler()
        s.finalize()
        with pytest.raises(RuntimeError):
            s.push_text("x")

    def test_tool_delta_after_finalize_raises(self):
        s = StreamAssembler()
        s.finalize()
        with pytest.raises(RuntimeError):
            s.push_tool_delta(index=0, name="x")

    def test_pending_empty_before_finalize(self):
        s = StreamAssembler()
        s.push_tool_delta(index=0, args_chunk="{{broken")
        assert s.pending == []   # pending is meaningful only post-finalize

    def test_tool_calls_view_available_mid_stream(self):
        s = StreamAssembler()
        s.push_tool_delta(index=0, name="x", args_chunk='{"a":1}')
        # Pre-finalize, args_parsed is None, args falls back to raw string
        current = s.tool_calls
        assert current[0]["name"] == "x"
        assert current[0]["args"] in ('{"a":1}', {"a": 1})
