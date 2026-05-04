"""Tests for v3.0.9:
  1. `/web` command wired into commands.py (the real REPL dispatcher).
  2. _wants_fresh_info detector that nudges the single-agent toward
     web_search + browse_url on info queries."""
from __future__ import annotations

import pytest


class TestWebCommandRegistered:
    def test_web_in_registry(self):
        from omnicli.commands import COMMAND_REGISTRY
        cmds = [c for c, _ in COMMAND_REGISTRY]
        assert "/web" in cmds

    def test_web_handled_by_dispatcher(self):
        from omnicli.commands import handle
        r = handle("/web")
        assert r.handled is True
        # Empty args shows usage, not "Unknown command"
        assert "Usage" in r.reply
        assert "<query>" in r.reply

    def test_web_not_unknown(self):
        """Regression: v3.0.8 dispatched /web through a separate registry
        that the REPL didn't use, so it printed 'Unknown command: /web'."""
        from omnicli.commands import handle
        r = handle("/web latest ipl")
        assert r.handled is True
        assert "Unknown command" not in r.reply

    def test_web_in_help(self):
        from omnicli.commands import handle
        r = handle("/help")
        assert "/web" in r.reply


class TestFreshInfoDetector:
    def _load(self):
        from omnicli.cli import _wants_fresh_info
        return _wants_fresh_info

    @pytest.mark.parametrize("prompt", [
        "get me latest IPL match for today",
        "what's the current bitcoin price",
        "today's news headlines",
        "search the internet for the latest cricket scores",
        "look up the stock price of Apple",
        "what's happening in the Ukraine war right now",
        "weather in Mumbai",
        "recent NASA announcements",
    ])
    def test_detects_fresh_info_queries(self, prompt):
        f = self._load()
        assert f(prompt) is True, f"'{prompt}' should flag fresh-info"

    @pytest.mark.parametrize("prompt", [
        "build a flask app",
        "explain how binary search works",
        "refactor this function for me",
        "summarise the book I just pasted",
        "what is a B-tree",
    ])
    def test_non_info_prompts_dont_trigger(self, prompt):
        f = self._load()
        assert f(prompt) is False, f"'{prompt}' should NOT flag fresh-info"

    def test_empty_safe(self):
        f = self._load()
        assert f("") is False
        assert f(None) is False   # type: ignore[arg-type]
