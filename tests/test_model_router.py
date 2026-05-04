"""Tests for model_router — turn classification + tier selection."""
from __future__ import annotations

import pytest

from omnicli import model_router as mr


@pytest.fixture(autouse=True)
def _reset_defaults():
    # Reset to hard-coded defaults so overrides in one test don't leak
    mr.set_tiers(
        cheap=     ("claude-haiku-4-5",  "https://api.anthropic.com/v1"),
        mid=       ("claude-sonnet-4-6", "https://api.anthropic.com/v1"),
        expensive= ("claude-opus-4-7",   "https://api.anthropic.com/v1"),
    )
    yield


class TestClassify:
    def test_empty_is_default(self):
        assert mr.classify("") == "default"

    def test_short_is_default(self):
        assert mr.classify("list files") == "default"

    def test_reasoning_cue(self):
        assert mr.classify("plan a migration strategy") == "reasoning"
        assert mr.classify("explain why this crashed") == "reasoning"
        assert mr.classify("help me debug this") == "reasoning"

    def test_long_prompt_is_reasoning(self):
        long = "x" * 600
        assert mr.classify(long) == "reasoning"

    def test_forced_tool_wins(self):
        # Even with reasoning cue + long, forced_tool dominates
        assert mr.classify("explain why " + "x" * 600, forced_tool=True) == "tool_assembly"

    def test_case_insensitive(self):
        assert mr.classify("DEBUG this crash") == "reasoning"


class TestRoute:
    def test_cheap_for_tool_assembly(self):
        m = mr.route(forced_tool=True)
        assert m.tier == "cheap"
        assert "haiku" in m.model.lower()

    def test_expensive_for_reasoning(self):
        m = mr.route("explain the architecture and tradeoffs")
        assert m.tier == "expensive"
        assert "opus" in m.model.lower()

    def test_mid_for_default(self):
        m = mr.route("list the files in /tmp")
        assert m.tier == "mid"
        assert "sonnet" in m.model.lower()

    def test_disabled_always_mid(self):
        m = mr.route("explain why this crashed", enabled=False)
        assert m.tier == "mid"
        assert "disabled" in m.reason

    def test_reason_is_populated(self):
        assert mr.route("list").reason == "standard turn → mid tier"
        assert mr.route(forced_tool=True).reason == "tool-only turn → cheap tier"


class TestTierOverride:
    def test_set_tiers_overrides_defaults(self):
        mr.set_tiers(
            cheap=("custom-cheap", "https://custom.example"),
        )
        m = mr.route(forced_tool=True)
        assert m.model == "custom-cheap"
        assert m.base_url == "https://custom.example"

    def test_config_override_wins_over_defaults(self, monkeypatch):
        """Config keys (tier_cheap_model / tier_cheap_url) override the
        in-process defaults."""
        from omnicli import memory
        memory.save_config("tier_cheap_model", "llama-3.1-8b-instant")
        memory.save_config("tier_cheap_url", "https://api.groq.com/openai/v1")
        m = mr.route(forced_tool=True)
        assert m.model == "llama-3.1-8b-instant"
        assert m.base_url == "https://api.groq.com/openai/v1"


class TestRouterEnabled:
    def test_default_disabled(self):
        assert mr.router_enabled() is False

    def test_toggle_on_via_config(self):
        from omnicli import memory
        memory.save_config("model_router_enabled", "true")
        assert mr.router_enabled() is True

    def test_toggle_off_via_config(self):
        from omnicli import memory
        memory.save_config("model_router_enabled", "false")
        assert mr.router_enabled() is False


class TestCostSavingsExpected:
    """Sanity: on a mix of 10 typical turns, cheap + mid should be the
    majority. This proves the heuristic favours cost savings."""
    def test_typical_mix_biased_cheap_mid(self):
        samples = [
            "ls",
            "run the tests",
            "write a file at /tmp/x.txt with content hi",
            "what's the content of /tmp/x.txt",
            "update package.json",
            "list processes",
            "check uptime",
            "show me git status",
            "git add and commit",
            "run the build",
        ]
        tiers = [mr.route(s).tier for s in samples]
        expensive_count = tiers.count("expensive")
        assert expensive_count <= 2, f"too many expensive turns: {tiers}"
