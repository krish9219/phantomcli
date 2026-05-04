"""Tests for the extracted text_tool_parser module."""
from __future__ import annotations

import pytest

from omnicli.text_tool_parser import parse_text_tool_calls, strip_tool_calls


class TestEmpty:
    def test_empty_string(self):
        assert parse_text_tool_calls("") == []

    def test_no_tool_calls_in_text(self):
        assert parse_text_tool_calls("just a normal reply") == []


class TestFormat1_GLM:
    def test_single_run_bash(self):
        text = (
            "Let me run that.\n"
            "<tool_call>run_bash\n"
            "<arg_key>command</arg_key><arg_value>ls -la</arg_value>\n"
            "</tool_call>"
        )
        calls = parse_text_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "run_bash"
        assert calls[0]["args"]["command"] == "ls -la"

    def test_write_file_with_two_args(self):
        text = (
            "<tool_call>write_file\n"
            "<arg_key>path</arg_key><arg_value>/tmp/x.py</arg_value>\n"
            "<arg_key>content</arg_key><arg_value>print('hi')</arg_value>\n"
            "</tool_call>"
        )
        calls = parse_text_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["args"] == {"path": "/tmp/x.py", "content": "print('hi')"}

    def test_unknown_tool_name_not_parsed(self):
        text = "<tool_call>made_up_tool\n<arg_key>x</arg_key><arg_value>y</arg_value></tool_call>"
        assert parse_text_tool_calls(text) == []


class TestFormat2_JSON:
    def test_arguments_key(self):
        text = '<tool_call>{"name": "run_bash", "arguments": {"command": "ls"}}</tool_call>'
        calls = parse_text_tool_calls(text)
        assert calls == [{"name": "run_bash", "args": {"command": "ls"}}]

    def test_parameters_key_as_alias(self):
        text = '<tool_call>{"name": "web_search", "parameters": {"query": "phantom"}}</tool_call>'
        calls = parse_text_tool_calls(text)
        assert calls == [{"name": "web_search", "args": {"query": "phantom"}}]

    def test_args_key_as_alias(self):
        text = '<tool_call>{"name": "read_file", "args": {"path": "/tmp/x"}}</tool_call>'
        calls = parse_text_tool_calls(text)
        assert calls == [{"name": "read_file", "args": {"path": "/tmp/x"}}]

    def test_unknown_tool_name_is_still_captured(self):
        """Format-2 (JSON) accepts any name — schema validation happens at dispatch."""
        text = '<tool_call>{"name": "custom_mcp_tool", "arguments": {"x": 1}}</tool_call>'
        calls = parse_text_tool_calls(text)
        assert calls == [{"name": "custom_mcp_tool", "args": {"x": 1}}]

    def test_malformed_json_falls_through(self):
        text = '<tool_call>{name: invalid json}</tool_call>'
        # Doesn't parse as JSON → falls through to format 1 → first line isn't
        # a known tool → falls through to format 3 → no match.
        assert parse_text_tool_calls(text) == []


class TestFormat3_FuncCall:
    def test_simple_func_call(self):
        text = '<tool_call>run_bash({"command": "ls"})</tool_call>'
        calls = parse_text_tool_calls(text)
        assert calls == [{"name": "run_bash", "args": {"command": "ls"}}]

    def test_malformed_json_inside_parens_skipped(self):
        text = '<tool_call>run_bash({not json})</tool_call>'
        assert parse_text_tool_calls(text) == []


class TestMultipleCalls:
    def test_two_different_calls_in_one_message(self):
        text = (
            'Here are two steps:\n'
            '<tool_call>{"name": "run_bash", "arguments": {"command": "pwd"}}</tool_call>\n'
            '<tool_call>{"name": "read_file", "arguments": {"path": "/etc/hosts"}}</tool_call>'
        )
        calls = parse_text_tool_calls(text)
        assert len(calls) == 2
        assert calls[0]["name"] == "run_bash"
        assert calls[1]["name"] == "read_file"

    def test_one_bad_one_good(self):
        text = (
            '<tool_call>{garbage</tool_call>\n'
            '<tool_call>{"name": "run_bash", "arguments": {"command": "ls"}}</tool_call>'
        )
        calls = parse_text_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "run_bash"


class TestCaseInsensitiveBlockTag:
    def test_uppercase_tool_call_tag(self):
        text = '<TOOL_CALL>{"name": "run_bash", "arguments": {"command": "ls"}}</TOOL_CALL>'
        calls = parse_text_tool_calls(text)
        assert len(calls) == 1


class TestStripToolCalls:
    def test_strip_removes_blocks(self):
        text = 'Before <tool_call>run_bash\n<arg_key>x</arg_key><arg_value>y</arg_value></tool_call> After'
        assert strip_tool_calls(text) == "Before  After"

    def test_strip_empty_returns_empty(self):
        assert strip_tool_calls("") == ""

    def test_strip_no_blocks_returns_input(self):
        assert strip_tool_calls("plain text") == "plain text"

    def test_strip_multiple_blocks(self):
        text = (
            'a <tool_call>x</tool_call> b <tool_call>y</tool_call> c'
        )
        # After both blocks are removed: "a  b  c" → stripped leading/trailing ws
        assert "tool_call" not in strip_tool_calls(text)


class TestEngineShimStillWorks:
    """The engine module re-exports the old names as shims; verify."""
    def test_engine_private_names_still_resolve(self):
        from omnicli import engine
        assert hasattr(engine, "_parse_text_tool_calls")
        assert hasattr(engine, "_strip_tool_calls")
        # And they behave the same as the new names
        text = '<tool_call>{"name": "run_bash", "arguments": {"command": "ls"}}</tool_call>'
        assert engine._parse_text_tool_calls(text) == parse_text_tool_calls(text)
