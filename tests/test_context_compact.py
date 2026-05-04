"""Tests for context_compact — token estimator + compaction policy."""
from __future__ import annotations

import pytest

from omnicli.context_compact import (
    count_tokens,
    estimate_messages,
    needs_compaction,
    compact,
    CompactionStats,
    MIN_MESSAGES_FOR_COMPACT,
    DEFAULT_KEEP_RECENT,
)


class TestTokenCounter:
    def test_empty_string_is_zero(self):
        assert count_tokens("") == 0

    def test_counts_scale_with_length(self):
        short = count_tokens("hello")
        longer = count_tokens("hello " * 100)
        assert longer > short * 50

    def test_dict_adds_overhead(self):
        """Message dict has a small per-message overhead."""
        assert count_tokens({"role": "user", "content": "hi"}) > count_tokens("hi")

    def test_list_sums(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
        total = count_tokens(msgs)
        per = count_tokens(msgs[0]) + count_tokens(msgs[1])
        assert total == per

    def test_multipart_content(self):
        """List-style content (e.g. text + tool_call) is summed per part."""
        t = count_tokens({
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_result", "input": "a" * 100},
            ],
        })
        assert t > 25  # hello + 100 chars + overhead

    def test_tiktoken_flag_tolerates_missing_tiktoken(self, monkeypatch):
        """PHANTOM_USE_TIKTOKEN=1 must not crash if tiktoken isn't installed."""
        monkeypatch.setenv("PHANTOM_USE_TIKTOKEN", "1")
        # Should still return something sensible via fallback.
        assert count_tokens("hello world") > 0


class TestNeedsCompaction:
    def _msgs(self, n, size=100):
        return [{"role": "user", "content": "x" * size} for _ in range(n)]

    def test_small_convo_never_compacts(self):
        msgs = self._msgs(4, size=10_000)
        # Even if huge, a 4-message convo is below MIN_MESSAGES_FOR_COMPACT
        assert needs_compaction(msgs, budget=1000) is False

    def test_over_budget_triggers_compaction(self):
        # 20 messages of ~1000 tokens each = ~20k tokens; budget 5000 ratio 0.5
        msgs = self._msgs(20, size=3_500)
        assert needs_compaction(msgs, budget=5000, ratio=0.5) is True

    def test_under_budget_does_not_compact(self):
        msgs = self._msgs(20, size=50)
        assert needs_compaction(msgs, budget=100_000) is False

    def test_zero_budget_returns_false(self):
        msgs = self._msgs(20, size=50)
        assert needs_compaction(msgs, budget=0) is False


class TestCompactShape:
    def _build(self, n_middle=20, n_tail=8):
        # 1 system + n_middle middle + n_tail tail
        msgs = [{"role": "system", "content": "You are Phantom."}]
        for i in range(n_middle):
            msgs.append({"role": "user" if i % 2 == 0 else "assistant",
                         "content": f"middle {i} " + "x" * 2000})
        for i in range(n_tail):
            msgs.append({"role": "user" if i % 2 == 0 else "assistant",
                         "content": f"tail {i}"})
        return msgs

    def test_compaction_reduces_tokens(self):
        msgs = self._build(n_middle=30, n_tail=8)
        new, stats = compact(msgs, budget=5000, ratio=0.5, keep_recent=8)
        assert stats.compressed is True
        assert stats.after_tokens < stats.before_tokens

    def test_preserves_all_system_messages(self):
        msgs = [
            {"role": "system", "content": "Sys A"},
            {"role": "system", "content": "Sys B"},
        ]
        for i in range(40):
            msgs.append({"role": "user", "content": f"m{i} " + "x" * 1000})
        new, stats = compact(msgs, budget=2000, ratio=0.5, keep_recent=6)
        system_in_new = [m for m in new if m.get("role") == "system"]
        # Original 2 system + 1 synthetic summary = 3 system-role messages
        contents = [m["content"] for m in system_in_new]
        assert any("Sys A" in c for c in contents)
        assert any("Sys B" in c for c in contents)
        assert any("COMPACTED CONTEXT" in c for c in contents)

    def test_preserves_last_keep_recent(self):
        msgs = self._build(n_middle=30, n_tail=10)
        new, _ = compact(msgs, budget=2000, ratio=0.5, keep_recent=10)
        # Last 10 non-system messages must survive unchanged
        tail = [m for m in new if m.get("role") != "system"][-10:]
        for i, m in enumerate(tail):
            assert m["content"] == f"tail {i}", f"tail message {i} mutated"

    def test_returns_same_object_when_no_compaction_needed(self):
        msgs = [{"role": "user", "content": "hi"} for _ in range(4)]
        new, stats = compact(msgs, budget=100_000)
        assert new is msgs
        assert stats.compressed is False

    def test_summary_includes_role_breakdown(self):
        msgs = [{"role": "system", "content": "S"}]
        for _ in range(20):
            msgs.append({"role": "user", "content": "x" * 2000})
            msgs.append({"role": "assistant", "content": "y" * 2000})
        new, stats = compact(msgs, budget=3000, ratio=0.5, keep_recent=4)
        assert "user:" in stats.summary_text
        assert "assistant:" in stats.summary_text
        assert "COMPACTED CONTEXT" in stats.summary_text

    def test_custom_summariser_invoked(self):
        msgs = [{"role": "system", "content": "S"}]
        for i in range(25):
            msgs.append({"role": "user", "content": "x" * 2000})
        captured = {}

        def my_summariser(old):
            captured["n"] = len(old)
            return f"SUMMARY_{len(old)}"

        new, stats = compact(msgs, budget=3000, ratio=0.5,
                             keep_recent=5, summariser=my_summariser)
        assert captured["n"] > 0
        assert stats.summary_text == f"SUMMARY_{captured['n']}"

    def test_summariser_failure_falls_back_to_default(self):
        msgs = [{"role": "system", "content": "S"}]
        for i in range(25):
            msgs.append({"role": "user", "content": "x" * 2000})

        def broken(_old):
            raise RuntimeError("oops")

        new, stats = compact(msgs, budget=3000, ratio=0.5,
                             keep_recent=5, summariser=broken)
        assert "COMPACTED CONTEXT" in stats.summary_text


class TestPreCompactHookFires:
    def test_pre_compact_fires_before_compaction(self, isolated_hooks_config, tmp_path):
        import json
        marker = tmp_path / "pre.touched"
        isolated_hooks_config.write_text(json.dumps({
            "PreCompact": [{"match": "*", "cmd": f"touch {marker}"}],
        }))
        msgs = [{"role": "system", "content": "S"}]
        for _ in range(25):
            msgs.append({"role": "user", "content": "x" * 2000})
        compact(msgs, budget=3000, ratio=0.5, keep_recent=5)
        assert marker.is_file()
