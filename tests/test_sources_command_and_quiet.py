"""Tests for v3.0.11:
  1. /sources command lists URLs from the last /web call
  2. browser.set_quiet silences 'Phantom Browser launching…' prints
  3. COMMAND_REGISTRY contains /sources entry"""
from __future__ import annotations

import pytest


class TestSourcesCommandRegistered:
    def test_in_registry(self):
        from omnicli.commands import COMMAND_REGISTRY
        cmds = [c for c, _ in COMMAND_REGISTRY]
        assert "/sources" in cmds

    def test_in_help(self):
        from omnicli.commands import handle
        r = handle("/help")
        assert "/sources" in r.reply

    def test_dispatches(self):
        from omnicli.commands import handle
        r = handle("/sources")
        assert r.handled is True
        # Without a prior /web, should show a helpful empty-state message
        assert "No /web call" in r.reply or "sources" in r.reply.lower()


class TestSourcesAfterWeb:
    def test_sources_lists_urls_after_web(self):
        """Seed the global stash manually to simulate a completed /web,
        then /sources should render URLs + first-line previews."""
        import omnicli.commands as _c
        _c._LAST_WEB_QUERY = "test query"
        _c._LAST_WEB_SOURCES = [
            ("https://example.com/a", "First line of page A\nmore content here"),
            ("https://example.com/b", "Only a single line on page B"),
        ]
        r = _c._sources("")
        assert "https://example.com/a" in r.reply
        assert "https://example.com/b" in r.reply
        assert "First line of page A" in r.reply
        assert "test query" in r.reply
        assert "[1]" in r.reply and "[2]" in r.reply

    def test_sources_empty_without_web(self):
        import omnicli.commands as _c
        _c._LAST_WEB_SOURCES = []
        _c._LAST_WEB_QUERY = ""
        r = _c._sources("")
        assert "No /web call" in r.reply


class TestBrowserQuietToggle:
    def test_set_quiet_globally(self):
        from omnicli import browser
        browser.set_quiet(True)
        assert browser._QUIET is True
        browser.set_quiet(False)
        assert browser._QUIET is False

    def test_log_respects_quiet_flag(self, capsys):
        from omnicli import browser
        browser.set_quiet(True)
        try:
            browser._log("[dim]this should NOT appear[/dim]")
            captured = capsys.readouterr()
            assert "should NOT appear" not in captured.out
        finally:
            browser.set_quiet(False)

    def test_log_prints_when_not_quiet(self, capsys):
        from omnicli import browser
        browser.set_quiet(False)
        browser._log("[dim]this SHOULD appear[/dim]")
        captured = capsys.readouterr()
        # rich prints without markup in tests but the text is there
        assert "SHOULD appear" in captured.out


class TestWebOutputIsClean:
    """Structural checks on the source code itself — no LLM call needed.
    Confirms the synth prompt rules are still in the source."""

    def test_synth_forbids_step_headings(self):
        """The synth prompt explicitly forbids 'Step 1:' / 'Step 2:' output."""
        import inspect, omnicli.commands as _c
        src = inspect.getsource(_c._web)
        assert "DO NOT write 'Step 1:'" in src, \
            "synth prompt missing Step-1 ban — model will regress to chain-of-thought"

    def test_synth_forbids_inline_source_citations(self):
        """Sources go into /sources, not inline [Source N] tags."""
        import inspect, omnicli.commands as _c
        src = inspect.getsource(_c._web)
        assert "DO NOT cite sources inline" in src, \
            "synth prompt missing inline-citation ban"

    def test_web_stashes_sources_globally(self):
        import inspect, omnicli.commands as _c
        src = inspect.getsource(_c._web)
        assert "_LAST_WEB_SOURCES" in src
        assert "_LAST_WEB_QUERY" in src

    def test_web_silences_browser_during_scrape(self):
        import inspect, omnicli.commands as _c
        src = inspect.getsource(_c._web)
        assert "set_quiet(True)" in src
        assert "set_quiet(False)" in src
