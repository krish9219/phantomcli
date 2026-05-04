"""
Research phase — runs BEFORE multi-agent build to pull real current data
from the web so the agents seed their app with facts, not LLM
hallucination.

The LLM (gpt-oss-120b, Claude, whatever) only knows what was in its
training cutoff. For any time-sensitive domain (cricket scores, stock
prices, news, weather forecasts, sports standings, crypto prices),
asking the model to "fabricate seed data" produces stale / invented
entries. v3.0.5's demo-banner was the honest-fallback fix; v3.0.6 adds
the actual fix: go fetch the real data first.

Flow:
  1. `detect_domain(directive)` — keyword classify: cricket, stocks,
     news, weather, crypto, sports, or None.
  2. For a matched domain, call `run_research(domain, directive)` which:
       a. Chooses 1-3 search queries tailored to that domain.
       b. Uses `_web_search` to find URLs (falls back to a curated list
          of known source URLs if search fails).
       c. Scrapes each URL via `run_browser` (Playwright → Jina →
          requests waterfall, already in browser.py).
       d. Extracts structured facts via simple regex/heuristic parsing
          — or, if a model client is available, asks the router model
          to summarize each scraped page into JSON.
       e. Writes the combined result to `<project_dir>/research.json`.
  3. The orchestrator tells every agent about `research.json` so the
     fetcher/seed-data writer uses it as primary input.

If any step fails (network down, bot-block, etc.), research.json stays
empty and the build proceeds as before with the demo banner.

Cost: adds 10-30s to build kickoff but eliminates the "showing matches
from past years" class of error.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("omnicli.research_phase")


# ─── Domain detection ────────────────────────────────────────────────────────

Domain = str  # string tag like "cricket", "stocks", etc.

# Each domain maps to (keywords, seed_queries, known_urls).
_DOMAIN_MAP: dict[Domain, dict] = {
    "cricket": {
        "keywords": ("ipl", "cricket", "t20", "odi", "test match", "bowler",
                     "batsman", "wicket", "runs", "over",),
        "queries": ("IPL 2026 latest match results", "IPL today's match schedule",
                    "IPL current season standings"),
        "urls": (
            "https://www.espncricinfo.com/series/indian-premier-league",
            "https://www.cricbuzz.com/cricket-match/live-scores",
            "https://en.wikipedia.org/wiki/Indian_Premier_League",
        ),
    },
    "stocks": {
        "keywords": ("stock", "ticker", "nasdaq", "nyse", "s&p", "dow jones",
                     "market cap", "earnings", "portfolio"),
        "queries": ("major stock market movers today",
                    "S&P 500 top gainers today"),
        "urls": (
            "https://finance.yahoo.com/gainers",
            "https://www.marketwatch.com/markets/us",
        ),
    },
    "crypto": {
        "keywords": ("crypto", "bitcoin", "btc", "eth", "ethereum", "coin",
                     "defi", "blockchain", "solana", "altcoin"),
        "queries": ("top 10 cryptocurrency prices today",
                    "bitcoin ethereum price now"),
        "urls": (
            "https://www.coingecko.com/",
            "https://coinmarketcap.com/",
        ),
    },
    "news": {
        "keywords": ("news", "headline", "breaking", "article",
                     "today's news", "latest news"),
        "queries": ("top news headlines today",),
        "urls": (
            "https://news.ycombinator.com/",
            "https://apnews.com/",
        ),
    },
    "weather": {
        "keywords": ("weather", "forecast", "temperature", "humidity",
                     "rain", "climate"),
        "queries": ("weather forecast today",),
        "urls": (),   # let search pick the city-specific URL
    },
    "sports": {
        "keywords": ("nba", "nfl", "football", "soccer", "tennis",
                     "formula 1", "f1", "basketball", "mlb"),
        "queries": ("latest sports results today",),
        "urls": (),
    },
}


def detect_domain(directive: str) -> Optional[Domain]:
    """Classify the directive into a supported research domain, or None
    if no domain matches (in which case research phase is skipped)."""
    if not directive:
        return None
    t = directive.lower()
    best: Optional[Domain] = None
    best_hits = 0
    for domain, spec in _DOMAIN_MAP.items():
        hits = sum(1 for kw in spec["keywords"] if kw in t)
        if hits > best_hits:
            best = domain
            best_hits = hits
    return best if best_hits >= 1 else None


# ─── Research runner ─────────────────────────────────────────────────────────


@dataclass
class ResearchResult:
    domain:        str
    directive:     str
    sources:       list[dict] = field(default_factory=list)
    raw_text:      str        = ""
    summary:       str        = ""
    structured:    dict       = field(default_factory=dict)
    fetched_at:    str        = ""
    ok:            bool       = False

    def as_dict(self) -> dict:
        return {
            "domain":    self.domain,
            "directive": self.directive,
            "sources":   self.sources,
            "summary":   self.summary,
            "structured": self.structured,
            "fetched_at": self.fetched_at,
            "ok":        self.ok,
        }


# Default cap so we don't run away on the scrape step.
_MAX_URLS_TO_SCRAPE = 3
_MAX_CHARS_PER_PAGE = 6000


def run_research(
    directive:   str,
    project_dir: str,
    on_status:   Optional[Callable[[str], None]] = None,
    max_urls:    int  = _MAX_URLS_TO_SCRAPE,
    summarize_with_llm: bool = True,
) -> ResearchResult:
    """Execute the research phase. Returns a ResearchResult whose
    .ok indicates whether we got usable data. Writes research.json
    into project_dir regardless (may be empty if everything fails)."""
    domain = detect_domain(directive)
    result = ResearchResult(
        domain=domain or "",
        directive=directive,
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if not domain:
        _write(project_dir, result)
        return result

    _emit(on_status, f"research: domain = {domain}")
    spec = _DOMAIN_MAP[domain]

    # ── Step 1: build URL list — search queries + curated seeds ──────────
    urls: list[str] = []
    try:
        from omnicli.engine import _web_search as _ws
        for q in spec["queries"]:
            raw = _ws(q, max_results=4)
            urls.extend(_extract_urls_from_search(raw))
            if len(urls) >= max_urls * 3:
                break
    except Exception as e:
        log.debug("web_search failed: %s", e)
    # Add curated URLs as fallback/augmentation
    for u in spec.get("urls", ()):
        if u not in urls:
            urls.append(u)
    urls = urls[: max_urls * 3]

    # ── Step 2: scrape — use existing browser waterfall ──────────────────
    try:
        from omnicli.browser import run_browser
    except Exception:
        run_browser = None

    scraped: list[tuple[str, str]] = []
    for u in urls:
        if len(scraped) >= max_urls:
            break
        _emit(on_status, f"research: scraping {u[:70]}")
        try:
            text = run_browser(u) if run_browser else ""
        except Exception as e:
            log.debug("scrape failed for %s: %s", u, e)
            text = ""
        if text and len(text) > 200 and "Could not fetch" not in text:
            scraped.append((u, text[:_MAX_CHARS_PER_PAGE]))
            result.sources.append({"url": u, "bytes": len(text)})

    if not scraped:
        _emit(on_status, "research: no pages scraped — seed fallback only")
        _write(project_dir, result)
        return result

    # ── Step 3: summarize + structure ────────────────────────────────────
    raw_combined = "\n\n---\n\n".join(f"[{u}]\n{t}" for u, t in scraped)
    result.raw_text = raw_combined

    if summarize_with_llm:
        try:
            result.structured = _llm_structure(domain, directive, scraped)
            result.summary = (json.dumps(result.structured, indent=2)
                              if result.structured else "")
        except Exception as e:
            log.debug("llm structure failed: %s", e)

    if not result.summary:
        # Fallback: first N chars of combined text
        result.summary = raw_combined[:3000]

    result.ok = True
    _write(project_dir, result)
    _emit(on_status, f"research: wrote research.json ({len(scraped)} sources)")
    return result


def _emit(on_status: Optional[Callable], msg: str) -> None:
    if on_status:
        try: on_status(msg)
        except Exception: pass
    log.info(msg)


def _extract_urls_from_search(raw: str) -> list[str]:
    """Pull plausible http(s) URLs out of a _web_search formatted string."""
    if not raw:
        return []
    # Strip search-result markers; grab every http(s)://... up to whitespace
    urls = re.findall(r'https?://[^\s\]\)\'"<>]+', raw)
    # De-dup, filter out obvious junk
    clean = []
    seen = set()
    for u in urls:
        u = u.rstrip(".,;:!?)")
        if u in seen:
            continue
        if any(bad in u.lower() for bad in (
            "google.com/url", "bing.com/ck", "duckduckgo.com/y.js",
        )):
            continue
        seen.add(u)
        clean.append(u)
    return clean


def _llm_structure(domain: str, directive: str,
                   scraped: list[tuple[str, str]]) -> dict:
    """Ask the router/main model to summarise scraped pages into a
    structured JSON blob the agents can seed from. Graceful degrade on
    error — returns {} and the caller uses raw text instead."""
    try:
        from openai import OpenAI
        from omnicli.memory import get_config
        from omnicli.auth import get_api_key
    except Exception:
        return {}
    key = get_api_key()
    if not key:
        return {}
    router_url   = (get_config("router_url", "")  or "").strip()
    router_model = (get_config("router_model", "") or "").strip()
    main_url     = (get_config("main_url", "")    or "").strip()
    main_model   = (get_config("main_model", "")  or "").strip()
    # Prefer the cheaper router model for summarization
    use_url   = router_url or main_url
    use_model = router_model or main_model
    if not use_model:
        return {}

    client = OpenAI(api_key=key, base_url=use_url or None)

    prompt_schema = _structure_hint(domain)
    scraped_text = "\n\n---\n\n".join(
        f"[SOURCE {i+1}: {u}]\n{t[:4000]}"
        for i, (u, t) in enumerate(scraped)
    )
    prompt = (
        f"You are doing RESEARCH for a developer building a '{directive}' app.\n\n"
        f"Below are {len(scraped)} web pages scraped live from the internet. "
        f"Extract the REAL, CURRENT data relevant to the request and return a "
        f"STRICT JSON object with the shape described below.\n\n"
        f"Required JSON shape:\n{prompt_schema}\n\n"
        f"Rules:\n"
        f"  - Use ONLY data present in the scraped pages. Do NOT invent.\n"
        f"  - If a page is irrelevant (navigation, ads, login pages), ignore it.\n"
        f"  - If you can't find enough data, return a smaller JSON — an empty\n"
        f"    array is acceptable if there's truly nothing relevant.\n"
        f"  - No markdown fences. No preamble. Response must start with `{{`.\n\n"
        f"SCRAPED PAGES:\n{scraped_text}"
    )
    try:
        resp = client.chat.completions.create(
            model=use_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _extract_json(raw) or {}
    except Exception as e:
        log.debug("llm summarize call failed: %s", e)
        return {}


def _extract_json(raw: str) -> Optional[dict]:
    """Same robust extractor we use in agents.plan() — handles markdown
    fences, chatty preamble, trailing commentary."""
    s = raw.strip()
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    for m in reversed(list(re.finditer(r"```(?:json|JSON)?\s*\n?([\s\S]*?)```", s))):
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    first = s.find("{")
    if first >= 0:
        for end in range(len(s), first, -1):
            if s[end - 1] != "}":
                continue
            try:
                parsed = json.loads(s[first:end])
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return None


def _structure_hint(domain: str) -> str:
    """Per-domain JSON schema example so the model knows what shape to
    produce. Kept terse."""
    if domain == "cricket":
        return ('{"yesterday":[{"team1":"","team2":"","venue":"","date":"",'
                '"score":"","top_scorer":"","best_bowling":"","summary":""}],'
                '"today":[{"team1":"","team2":"","venue":"","start_time_ist":"",'
                '"preview":""}],"upcoming":[{"team1":"","team2":"","venue":"",'
                '"date":"","preview":""}]}')
    if domain == "stocks":
        return ('{"gainers":[{"symbol":"","name":"","price":0,"change_pct":0}],'
                '"losers":[{"symbol":"","name":"","price":0,"change_pct":0}]}')
    if domain == "crypto":
        return ('{"coins":[{"symbol":"","name":"","price_usd":0,'
                '"market_cap":0,"change_24h_pct":0}]}')
    if domain == "news":
        return ('{"headlines":[{"title":"","source":"","url":"","summary":"","ts":""}]}')
    if domain == "weather":
        return ('{"locations":[{"city":"","country":"","temp_c":0,'
                '"condition":"","forecast_next_days":[]}]}')
    if domain == "sports":
        return ('{"matches":[{"home":"","away":"","league":"","date":"",'
                '"score":"","summary":""}]}')
    return '{"entries":[]}'


def _write(project_dir: str, result: ResearchResult) -> str:
    try:
        os.makedirs(project_dir, exist_ok=True)
    except OSError:
        pass
    path = os.path.join(project_dir, "research.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.as_dict(), f, indent=2, default=str)
    except OSError as e:
        log.warning("could not write research.json: %s", e)
    return path


__all__ = ["detect_domain", "run_research", "ResearchResult"]
