"""Tests for the v3.0.8 routing fix: info queries + negated prompts no
longer spawn multi-agent builds."""
from __future__ import annotations

import pytest

from omnicli.agents import AgentOrchestrator


class TestNegations:
    """Explicit 'don't build' / 'forget about the app' MUST NOT trigger."""

    @pytest.mark.parametrize("prompt", [
        "forget about the web app I am asking you to search for the latest ipl match details",
        "forget the dashboard, just tell me who won yesterday",
        "don't make a project, just tell me the score",
        "no project please, just search for ipl news",
        "no new project — give me a summary",
        "not a project — just explain how x works",
        "just search for bitcoin price",
        "just tell me about ipl 2026",
        "only search and report back",
        "without building anything, what's the weather in delhi",
        "skip the project, just fetch today's news",
    ])
    def test_negation_blocks_spawn(self, prompt):
        assert AgentOrchestrator.should_spawn(prompt) is False, \
            f"'{prompt}' should NOT trigger multi-agent"


class TestInfoQueries:
    """Classic question-style prompts go to single-agent, not builds."""

    @pytest.mark.parametrize("prompt", [
        "what is the latest IPL match today",
        "who won the cricket match yesterday",
        "when is the next flight from Mumbai to Delhi",
        "tell me about the 2026 IPL season",
        "show me the top 10 bitcoin prices",
        "summarize the latest tech news",
        "compare Python 3.11 and 3.12",
        "analyze the recent stock market trends",
        "find me the current weather in Hyderabad",
        "search for iPhone 17 reviews",
        "get me the latest cricket score",
        "latest news on climate change",
        "current bitcoin price",
    ])
    def test_info_queries_dont_spawn(self, prompt):
        assert AgentOrchestrator.should_spawn(prompt) is False, \
            f"'{prompt}' should NOT trigger multi-agent"


class TestInfoQueryWithBuildVerbOverride:
    """If the info query ALSO contains a build verb ('compare X and then
    build an app'), the build wins — user explicitly asked for code."""

    def test_compare_then_build_triggers(self):
        assert AgentOrchestrator.should_spawn(
            "compare pandas vs polars, then build a dashboard showing both"
        ) is True

    def test_tell_me_then_make_triggers(self):
        # "make" is a WEAK keyword + long prompt → True
        assert AgentOrchestrator.should_spawn(
            "tell me about weather apis and make a weather tracker for 10 cities"
        ) is True


class TestBuildRequestsStillTrigger:
    """Regression guard — legitimate build requests must still spawn."""

    @pytest.mark.parametrize("prompt", [
        "create a IPL match dashboard with live data",
        "build a flask web app for cricket scores",
        "make a react dashboard showing stocks",
        "scaffold a FastAPI backend with sqlite",
        "build me an ml model for churn prediction",
    ])
    def test_build_requests_still_spawn(self, prompt):
        assert AgentOrchestrator.should_spawn(prompt) is True, \
            f"'{prompt}' should STILL trigger multi-agent"


class TestEmptyAndEdgeCases:
    def test_empty(self):
        assert AgentOrchestrator.should_spawn("") is False

    def test_none_safe(self):
        assert AgentOrchestrator.should_spawn(None) is False   # type: ignore[arg-type]

    def test_short_greeting(self):
        assert AgentOrchestrator.should_spawn("hi") is False

    def test_standalone_build_word_no_context(self):
        # "build" alone without length or STRONG keyword → no spawn
        assert AgentOrchestrator.should_spawn("build") is False


class TestWebSlashCommand:
    def test_web_command_registered(self):
        from omnicli.slash_commands import DEFAULT_REGISTRY
        cmd = DEFAULT_REGISTRY.get("web")
        assert cmd is not None
        assert "search" in cmd.description.lower()
        assert cmd.usage == "/web <query>"

    def test_web_empty_args_shows_usage(self):
        from omnicli.slash_commands import dispatch
        r = dispatch("/web")
        assert r.error is True
        assert "Usage" in r.text
        assert "/web <query>" in r.text

    def test_web_in_help(self):
        from omnicli.slash_commands import dispatch
        r = dispatch("/help")
        assert "/web" in r.text
