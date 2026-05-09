"""Web search — looks up the open web via DuckDuckGo HTML by default,
upgrades to Brave or Tavily when an API key is available.

The agent calls this when the user asks about live/current/recent
information (sports scores, news, today's prices, recent commits on a
GitHub project, etc.). The result is a list of {title, url, snippet}
dicts; the agent typically calls ``web_fetch`` afterward to read the
full page of the most relevant hit.

Provider priority (first one with a usable key wins):
  1. Brave Search   — env: BRAVE_SEARCH_API_KEY
  2. Tavily         — env: TAVILY_API_KEY
  3. DuckDuckGo HTML scrape — no key needed (fallback)

The DuckDuckGo path scrapes ``html.duckduckgo.com`` which has been
deliberately stable for ~5 years; if/when it breaks, the search call
returns an explicit error with a hint so the model can fall back to
web_fetch on a known URL instead.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from phantom.errors import PhantomError

__all__ = ["SearchHit", "web_search"]


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str


def web_search(
    *,
    query: str,
    max_results: int = 6,
    timeout_s: float = 12.0,
    client: Any = None,
) -> list[SearchHit]:
    """Run a web search. Returns up to ``max_results`` hits."""
    if not isinstance(query, str) or not query.strip():
        raise PhantomError("web_search: 'query' must be a non-empty string")
    max_results = max(1, min(int(max_results), 20))

    if client is None:
        import httpx
        client = httpx.Client(
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "phantom/web_search"},
        )
        owns_client = True
    else:
        owns_client = False
    try:
        brave_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        if brave_key:
            return _brave_search(client, query, max_results, brave_key)
        tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
        if tavily_key:
            return _tavily_search(client, query, max_results, tavily_key)
        return _ddg_search(client, query, max_results)
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:
                pass


# ─── Brave ───────────────────────────────────────────────────────────────────

def _brave_search(client, query: str, n: int, key: str) -> list[SearchHit]:
    response = client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": n},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    if response.status_code != 200:
        raise PhantomError(
            f"Brave search returned {response.status_code}: {response.text[:200]}"
        )
    data = response.json() or {}
    web_results = (data.get("web") or {}).get("results") or []
    out: list[SearchHit] = []
    for r in web_results[:n]:
        if not isinstance(r, dict):
            continue
        out.append(SearchHit(
            title=str(r.get("title", "")),
            url=str(r.get("url", "")),
            snippet=str(r.get("description", "")),
        ))
    return out


# ─── Tavily ──────────────────────────────────────────────────────────────────

def _tavily_search(client, query: str, n: int, key: str) -> list[SearchHit]:
    response = client.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": n,
              "include_answer": False, "search_depth": "basic"},
        headers={"Accept": "application/json"},
    )
    if response.status_code != 200:
        raise PhantomError(
            f"Tavily search returned {response.status_code}: {response.text[:200]}"
        )
    data = response.json() or {}
    out: list[SearchHit] = []
    for r in (data.get("results") or [])[:n]:
        if not isinstance(r, dict):
            continue
        out.append(SearchHit(
            title=str(r.get("title", "")),
            url=str(r.get("url", "")),
            snippet=str(r.get("content", "")),
        ))
    return out


# ─── DuckDuckGo HTML scrape (no key fallback) ───────────────────────────────

_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_AMP_RE = re.compile(r"&(amp|lt|gt|quot|#39);")


def _strip_html(s: str) -> str:
    s = _TAG_RE.sub("", s)
    s = _AMP_RE.sub(
        lambda m: {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "#39": "'"}.get(
            m.group(1), ""
        ),
        s,
    )
    return s.strip()


def _ddg_unwrap(url: str) -> str:
    """DuckDuckGo wraps result links as `/l/?uddg=ENCODED`. Decode if so."""
    from urllib.parse import unquote
    m = re.search(r"uddg=([^&]+)", url)
    if m:
        return unquote(m.group(1))
    return url


def _ddg_search(client, query: str, n: int) -> list[SearchHit]:
    response = client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; phantom/web_search)"},
    )
    if response.status_code != 200:
        raise PhantomError(
            f"DuckDuckGo HTML returned {response.status_code}. "
            f"Set BRAVE_SEARCH_API_KEY or TAVILY_API_KEY for a more reliable backend."
        )
    out: list[SearchHit] = []
    for m in _DDG_RESULT_RE.finditer(response.text):
        url = _ddg_unwrap(m.group("url"))
        out.append(SearchHit(
            title=_strip_html(m.group("title")),
            url=url,
            snippet=_strip_html(m.group("snippet")),
        ))
        if len(out) >= n:
            break
    if not out:
        raise PhantomError(
            "DuckDuckGo HTML returned no parseable results. The page format "
            "may have changed; set BRAVE_SEARCH_API_KEY for a stable API."
        )
    return out
