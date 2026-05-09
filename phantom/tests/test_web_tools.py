"""Tests for v1.1.21 web_search + web_fetch agent tool registration.

The motivating user trace: asked Phantom for live cricket scores
(GT vs RR) and got "I don't have access to real-time data". Phantom
*has* web_fetch in the codebase but it wasn't registered as an agent
tool, and there was no web_search at all. Both are now wired.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from phantom.agent.tools import _web_fetch, _web_search, default_tools
from phantom.tools.web_search import _ddg_search, _ddg_unwrap, _strip_html


# ─── _strip_html / _ddg_unwrap utilities ─────────────────────────────────────

def test_strip_html_removes_tags_and_entities():
    assert _strip_html("<b>hi &amp; bye</b>") == "hi & bye"
    assert _strip_html("&lt;tag&gt;") == "<tag>"


def test_ddg_unwrap_decodes_real_url():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%2Fb"
    assert _ddg_unwrap(wrapped) == "https://example.com/a/b"


def test_ddg_unwrap_passes_through_plain_url():
    plain = "https://example.com/x"
    assert _ddg_unwrap(plain) == plain


# ─── _ddg_search parses results ──────────────────────────────────────────────

_FAKE_DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcricbuzz.com%2Fmatch%2F123" rel="nofollow">GT vs RR — Live Score</a>
  <a class="result__snippet">Gujarat Titans 165/4 (18.0) vs Rajasthan Royals — chasing 192</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fespncricinfo.com%2Fmatch%2F456">Match Centre — IPL 2024</a>
  <a class="result__snippet">Full scorecard, ball-by-ball commentary</a>
</div>
</body></html>
"""


def test_ddg_search_extracts_title_url_snippet():
    fake_resp = MagicMock(status_code=200, text=_FAKE_DDG_HTML)
    fake_client = MagicMock()
    fake_client.get.return_value = fake_resp
    hits = _ddg_search(fake_client, "GT vs RR score", n=5)
    assert len(hits) == 2
    assert hits[0].title == "GT vs RR — Live Score"
    assert hits[0].url == "https://cricbuzz.com/match/123"
    assert "165/4" in hits[0].snippet
    assert hits[1].url.startswith("https://espncricinfo.com")


def test_ddg_search_raises_when_no_results_parseable():
    """If DuckDuckGo changes layout, raise so the caller (and the user)
    knows to set BRAVE_SEARCH_API_KEY."""
    fake_resp = MagicMock(status_code=200, text="<html>nothing here</html>")
    fake_client = MagicMock()
    fake_client.get.return_value = fake_resp
    from phantom.errors import PhantomError
    with pytest.raises(PhantomError, match="no parseable results"):
        _ddg_search(fake_client, "x", n=5)


# ─── _web_search agent wrapper ────────────────────────────────────────────────

def test_web_search_empty_query_returns_hint():
    out = json.loads(_web_search({"query": ""}))
    assert "error" in out
    assert "hint" in out


def test_web_search_returns_hits_as_json(monkeypatch):
    """Wrapper should return a JSON list of hit dicts."""
    from phantom.tools.web_search import SearchHit
    fake_hits = [
        SearchHit(title="A", url="https://a.test", snippet="snip A"),
        SearchHit(title="B", url="https://b.test", snippet="snip B"),
    ]
    monkeypatch.setattr(
        "phantom.agent.tools.web_search",
        lambda *, query, max_results=6: fake_hits,
    )
    out = json.loads(_web_search({"query": "x", "max_results": 2}))
    assert isinstance(out, list)
    assert out == [
        {"title": "A", "url": "https://a.test", "snippet": "snip A"},
        {"title": "B", "url": "https://b.test", "snippet": "snip B"},
    ]


def test_web_search_returns_error_on_phantom_error(monkeypatch):
    from phantom.errors import PhantomError as _PE
    def boom(*, query, max_results=6):
        raise _PE("upstream broken")
    monkeypatch.setattr("phantom.agent.tools.web_search", boom)
    out = json.loads(_web_search({"query": "x"}))
    assert "error" in out
    assert "upstream broken" in out["error"]


# ─── _web_fetch agent wrapper ─────────────────────────────────────────────────

def test_web_fetch_empty_url_returns_hint():
    out = json.loads(_web_fetch({"url": ""}))
    assert "error" in out
    assert "hint" in out


def test_web_fetch_returns_truncated_body(monkeypatch):
    from phantom.tools.web_fetch import WebFetchResult
    monkeypatch.setattr(
        "phantom.agent.tools.web_fetch",
        lambda *, url, max_bytes=256 * 1024: WebFetchResult(
            ok=True, url=url, status=200,
            content_type="text/html", text="hello world" * 1000,
            truncated=False,
        ),
    )
    out = json.loads(_web_fetch({"url": "https://example.com"}))
    assert out["ok"] is True
    assert out["status"] == 200
    # Body got chopped at the wrapper's 8KB cap (the helper itself is set
    # to 256KB; the wrapper trims further to keep the model context sane).
    assert len(out["text"]) <= 8192
    assert out["truncated"] is True


def test_web_fetch_propagates_fetch_error(monkeypatch):
    from phantom.tools.web_fetch import WebFetchResult
    monkeypatch.setattr(
        "phantom.agent.tools.web_fetch",
        lambda *, url, max_bytes=256 * 1024: WebFetchResult(
            ok=False, url=url, error="refusing private/internal host",
        ),
    )
    out = json.loads(_web_fetch({"url": "http://10.0.0.1/"}))
    assert "error" in out
    assert "private" in out["error"]


# ─── tool registration ──────────────────────────────────────────────────────

def test_default_tools_includes_web_search_and_web_fetch(tmp_path: Path):
    names = [t.name for t in default_tools(workdir=str(tmp_path))]
    assert "web_search" in names
    assert "web_fetch" in names


def test_web_search_schema_advertises_query(tmp_path: Path):
    tools = default_tools(workdir=str(tmp_path))
    ws = next(t for t in tools if t.name == "web_search")
    assert "query" in ws.input_schema["properties"]
    assert "query" in ws.input_schema["required"]
    desc = ws.description.lower()
    assert "current" in desc or "live" in desc or "recent" in desc


def test_web_fetch_schema_advertises_url(tmp_path: Path):
    tools = default_tools(workdir=str(tmp_path))
    wf = next(t for t in tools if t.name == "web_fetch")
    assert "url" in wf.input_schema["properties"]
    assert "url" in wf.input_schema["required"]
