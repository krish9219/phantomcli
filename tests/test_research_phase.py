"""Tests for research_phase — domain detection, URL extraction, JSON
repair, result persistence. Scraping itself is NOT tested live — we
inject mocks for browser + search + LLM."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from omnicli.research_phase import (
    detect_domain, run_research, _extract_urls_from_search,
    _extract_json, _structure_hint, ResearchResult, _DOMAIN_MAP,
)


class TestDomainDetection:
    @pytest.mark.parametrize("directive,expected", [
        ("build a live IPL cricket dashboard", "cricket"),
        ("show T20 match scores", "cricket"),
        ("create a stock ticker dashboard", "stocks"),
        ("top S&P 500 gainers today", "stocks"),
        ("bitcoin price tracker webapp", "crypto"),
        ("show ETH and BTC prices", "crypto"),
        ("latest news headlines dashboard", "news"),
        ("weather forecast for multiple cities", "weather"),
        ("NBA playoff standings", "sports"),
    ])
    def test_detects_known_domains(self, directive, expected):
        assert detect_domain(directive) == expected

    @pytest.mark.parametrize("directive", [
        "build a todo list app",
        "create a calculator",
        "make a snake game",
        "simple hello world flask app",
        "",
    ])
    def test_no_domain_returns_none(self, directive):
        assert detect_domain(directive) is None

    def test_strongest_signal_wins(self):
        # Mixed words — cricket has 2 hits, stocks has 1 → cricket wins
        d = detect_domain("IPL cricket stock portfolio app")
        assert d == "cricket"


class TestUrlExtractor:
    def test_pulls_http_urls(self):
        raw = "Result 1: https://example.com/x Result 2: https://foo.bar/y"
        urls = _extract_urls_from_search(raw)
        assert "https://example.com/x" in urls
        assert "https://foo.bar/y" in urls

    def test_strips_trailing_punctuation(self):
        raw = "See https://example.com/page."
        urls = _extract_urls_from_search(raw)
        assert "https://example.com/page" in urls

    def test_filters_tracker_redirects(self):
        raw = "https://google.com/url?q=blah https://example.com/real"
        urls = _extract_urls_from_search(raw)
        assert "https://example.com/real" in urls
        assert not any("google.com/url" in u for u in urls)

    def test_dedupes(self):
        raw = "https://a.com https://a.com https://b.com"
        urls = _extract_urls_from_search(raw)
        assert urls.count("https://a.com") == 1

    def test_empty_safe(self):
        assert _extract_urls_from_search("") == []


class TestJsonExtractor:
    def test_clean_object(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_with_preamble(self):
        raw = 'Here is the JSON:\n```json\n{"ok": true}\n```\nCheers.'
        assert _extract_json(raw) == {"ok": True}

    def test_garbage_returns_none(self):
        assert _extract_json("not json") is None

    def test_array_returns_none(self):
        # _extract_json (here) returns dict-only — arrays aren't valid root
        assert _extract_json("[1, 2, 3]") is None

    def test_trailing_commentary(self):
        raw = '{"coins": []}\nEnd of response.'
        assert _extract_json(raw) == {"coins": []}


class TestStructureHints:
    def test_has_hint_for_every_domain(self):
        for domain in _DOMAIN_MAP:
            hint = _structure_hint(domain)
            assert hint.startswith("{") and hint.endswith("}"), \
                f"hint for {domain} isn't JSON-shaped"

    def test_fallback_for_unknown(self):
        h = _structure_hint("zzz-unknown")
        assert "entries" in h


class TestRunResearchWritesFile:
    def test_unknown_domain_writes_empty_file(self, tmp_path):
        project_dir = tmp_path / "project_x"
        project_dir.mkdir()
        result = run_research(
            directive="build a todo app",
            project_dir=str(project_dir),
        )
        assert result.domain == ""
        assert result.ok is False
        research_file = project_dir / "research.json"
        assert research_file.is_file()
        data = json.loads(research_file.read_text())
        assert data["ok"] is False

    def test_domain_detected_but_scrape_fails_writes_empty(
        self, tmp_path, monkeypatch,
    ):
        """Simulate total network failure — research still writes a file
        (the orchestrator reads its .ok to decide whether to tell agents
        to seed from research.json or fall back)."""
        project_dir = tmp_path / "project_y"
        project_dir.mkdir()
        # Stub the scraper to always return empty
        import omnicli.browser as _b
        monkeypatch.setattr(_b, "run_browser", lambda u: "")
        # Stub the search too
        import omnicli.engine as _e
        monkeypatch.setattr(_e, "_web_search", lambda q, max_results=4: "")

        result = run_research(
            directive="build a cricket IPL dashboard",
            project_dir=str(project_dir),
            summarize_with_llm=False,
        )
        assert result.domain == "cricket"
        assert result.ok is False
        assert result.sources == []
        research_file = project_dir / "research.json"
        assert research_file.is_file()

    def test_scrape_succeeds_records_sources(self, tmp_path, monkeypatch):
        """One URL returns a big payload — research marks .ok=True and
        stores the source entry."""
        project_dir = tmp_path / "project_z"
        project_dir.mkdir()

        import omnicli.browser as _b
        import omnicli.engine as _e

        def fake_search(q, max_results=4):
            return "Result: https://example.com/matches"
        def fake_scrape(url):
            # Large enough to pass the >200 char threshold
            return "Match: MI vs CSK " * 100

        monkeypatch.setattr(_e, "_web_search", fake_search)
        monkeypatch.setattr(_b, "run_browser", fake_scrape)

        result = run_research(
            directive="IPL cricket dashboard",
            project_dir=str(project_dir),
            summarize_with_llm=False,
        )
        assert result.ok is True
        assert len(result.sources) >= 1
        assert any("example.com" in s["url"] for s in result.sources)
        data = json.loads((project_dir / "research.json").read_text())
        assert data["ok"] is True
        assert data["summary"]


class TestResearchResultShape:
    def test_as_dict_serialisable(self):
        r = ResearchResult(
            domain="cricket", directive="x",
            sources=[{"url": "https://a", "bytes": 1}],
            summary="hi", structured={"k": 1}, fetched_at="t", ok=True,
        )
        d = r.as_dict()
        # Round-trips through json without error
        reparsed = json.loads(json.dumps(d))
        assert reparsed["domain"] == "cricket"
        assert reparsed["ok"] is True
        assert reparsed["structured"] == {"k": 1}
