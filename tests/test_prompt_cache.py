"""Tests for prompt_cache — cache_control annotation for Anthropic-style
prompt caching."""
from __future__ import annotations

import pytest

from omnicli.prompt_cache import (
    annotate_system, annotate_long_blocks,
    cached_block_count, strip_cache_controls,
    DEFAULT_MIN_SYSTEM_TOKENS, DEFAULT_MIN_BLOCK_TOKENS,
)


def _long(chars: int) -> str:
    return "x" * chars


class TestAnnotateSystem:
    def test_marks_single_long_system(self):
        msgs = [
            {"role": "system", "content": _long(10_000)},
            {"role": "user",   "content": "hello"},
        ]
        out = annotate_system(msgs)
        assert isinstance(out[0]["content"], list)
        assert out[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_short_system_not_marked(self):
        msgs = [
            {"role": "system", "content": "short"},
            {"role": "user",   "content": "hi"},
        ]
        out = annotate_system(msgs)
        assert cached_block_count(out) == 0
        # Left untouched (still a string)
        assert out[0]["content"] == "short"

    def test_custom_min_threshold(self):
        msgs = [
            {"role": "system", "content": _long(500)},  # ~140 tokens
            {"role": "user",   "content": "hi"},
        ]
        # Above our custom tiny threshold → marked
        out = annotate_system(msgs, min_tokens=50)
        assert cached_block_count(out) == 1

    def test_multiple_system_messages_merged(self):
        msgs = [
            {"role": "system", "content": _long(5_000)},
            {"role": "system", "content": _long(5_000)},
            {"role": "user",   "content": "hi"},
        ]
        out = annotate_system(msgs)
        # Merged into one system message
        system_count = sum(1 for m in out if m["role"] == "system")
        assert system_count == 1
        assert cached_block_count(out) == 1
        # Content is a list of two text blocks
        assert len(out[0]["content"]) == 2

    def test_no_leading_system_is_noop(self):
        msgs = [
            {"role": "user",      "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        out = annotate_system(msgs, min_tokens=1)
        assert cached_block_count(out) == 0
        assert out == msgs

    def test_empty_returns_empty(self):
        assert annotate_system([]) == []

    def test_persistent_cache_type(self):
        msgs = [{"role": "system", "content": _long(10_000)}]
        out = annotate_system(msgs, cache_type="persistent")
        assert out[0]["content"][-1]["cache_control"] == {"type": "persistent"}

    def test_preserves_non_system_tail(self):
        msgs = [
            {"role": "system", "content": _long(10_000)},
            {"role": "user",   "content": "question 1"},
            {"role": "assistant", "content": "answer 1"},
            {"role": "user",   "content": "question 2"},
        ]
        out = annotate_system(msgs)
        assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
        assert out[1]["content"] == "question 1"
        assert out[3]["content"] == "question 2"

    def test_returns_new_list(self):
        msgs = [{"role": "system", "content": _long(10_000)}]
        out = annotate_system(msgs)
        assert out is not msgs


class TestAnnotateLongBlocks:
    def test_marks_long_user_message(self):
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user",   "content": _long(30_000)},
            {"role": "assistant", "content": _long(30_000)},  # assistant skipped
        ]
        out = annotate_long_blocks(msgs)
        assert cached_block_count(out) == 1
        # The user message is the one marked
        user = next(m for m in out if m["role"] == "user")
        assert isinstance(user["content"], list)
        assert user["content"][-1]["cache_control"]["type"] == "ephemeral"

    def test_assistant_excluded_by_default(self):
        msgs = [
            {"role": "assistant", "content": _long(30_000)},
        ]
        out = annotate_long_blocks(msgs)
        assert cached_block_count(out) == 0

    def test_short_messages_ignored(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "there"},
        ]
        out = annotate_long_blocks(msgs)
        assert cached_block_count(out) == 0

    def test_custom_skip_roles(self):
        msgs = [{"role": "user", "content": _long(30_000)}]
        out = annotate_long_blocks(msgs, skip_roles=("user",))
        assert cached_block_count(out) == 0

    def test_custom_threshold(self):
        msgs = [{"role": "user", "content": _long(300)}]
        # Threshold tiny → marked
        out = annotate_long_blocks(msgs, min_tokens=10)
        assert cached_block_count(out) == 1

    def test_list_content_preserved(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": _long(10_000)},
            {"type": "text", "text": _long(20_000)},
        ]}]
        out = annotate_long_blocks(msgs)
        content = out[0]["content"]
        assert len(content) == 2
        # Only the LAST block is marked (Anthropic caches up-to-and-including)
        assert "cache_control" not in content[0]
        assert content[1]["cache_control"]["type"] == "ephemeral"


class TestStripCacheControls:
    def test_removes_all_markers(self):
        msgs = [
            {"role": "system", "content": [
                {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
            ]},
        ]
        out = strip_cache_controls(msgs)
        assert "cache_control" not in out[0]["content"][0]
        # Text and type survive
        assert out[0]["content"][0]["text"] == "hi"
        assert out[0]["content"][0]["type"] == "text"

    def test_string_content_unchanged(self):
        msgs = [{"role": "user", "content": "plain"}]
        out = strip_cache_controls(msgs)
        assert out == msgs

    def test_returns_new_list(self):
        msgs = [{"role": "user", "content": "x"}]
        assert strip_cache_controls(msgs) is not msgs


class TestCachedBlockCount:
    def test_counts_across_messages(self):
        msgs = [
            {"role": "system", "content": [
                {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "b"},  # no cache_control
            ]},
            {"role": "user", "content": [
                {"type": "text", "text": "c", "cache_control": {"type": "ephemeral"}},
            ]},
        ]
        assert cached_block_count(msgs) == 2

    def test_no_blocks_returns_zero(self):
        msgs = [{"role": "user", "content": "plain string"}]
        assert cached_block_count(msgs) == 0


class TestComposition:
    def test_annotate_both_system_and_long_user(self):
        msgs = [
            {"role": "system", "content": _long(10_000)},
            {"role": "user",   "content": _long(30_000)},
        ]
        out = annotate_long_blocks(annotate_system(msgs))
        # 1 system cache + 1 user cache = 2
        assert cached_block_count(out) == 2
