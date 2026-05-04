import json
import logging
import os
import platform
import re
import tempfile
import uuid
from pathlib import Path
from openai import OpenAI

log = logging.getLogger("omnicli.engine")
from rich.console import Console
from omnicli.memory import get_config
from omnicli.auth import get_api_key
from omnicli.executor import execute_bash
from omnicli.browser import run_browser
from omnicli.tasks import TaskTracker

_IS_WINDOWS = platform.system() == "Windows"
_SHELL_NAME = "PowerShell/CMD" if _IS_WINDOWS else ("bash/zsh" if platform.system() == "Darwin" else "bash")

console = Console()

MAX_ROUNDS = 24
"""Maximum tool-call rounds per turn. Override with config key `max_tool_rounds`
(min 1, max 64). 6 was the old default — far too low for multi-file builds,
which made the agent give up mid-project. See `_max_rounds()` below."""


def _max_rounds() -> int:
    """Resolve the effective round cap from config, with hard bounds."""
    try:
        from omnicli.memory import get_config as _gc
        raw = _gc("max_tool_rounds", str(MAX_ROUNDS)) or MAX_ROUNDS
        n = int(raw)
        return max(1, min(n, 64))
    except (TypeError, ValueError, ImportError):
        return MAX_ROUNDS


# ─── TEXT TOOL CALL PARSER ────────────────────────────────────────────────────
# Some models (GLM, Llama variants) emit tool calls as XML text rather than
# using the structured API. This parser handles all three formats.

# The text tool-call parser has been extracted to omnicli.text_tool_parser
# for isolation and testability. We keep the `_`-prefixed names here as
# shims so existing call sites (and any third-party code) still work.

from omnicli.text_tool_parser import (
    parse_text_tool_calls as _parse_text_tool_calls,
    strip_tool_calls as _strip_tool_calls,
)


# ─── DYNAMIC PERSONA ──────────────────────────────────────────────────────────

# Words / short phrases that indicate casual conversation — never shapeshift for these
_SMALL_TALK_TOKENS = (
    "hi", "hello", "hey", "yo", "sup", "hola", "howdy",
    "good morning", "good afternoon", "good evening", "good night",
    "how are you", "how're you", "how r u", "how are u",
    "how's it going", "whats up", "what's up", "wassup",
    "thanks", "thank you", "thx", "ty ",
    "bye", "goodbye", "see you", "cya",
    "nice", "cool", "ok", "okay", "great",
    "who are you", "what can you do", "what's your name",
)

# Keyword → persona mapping for instant (zero-API-cost) persona detection.
_PERSONA_MAP: list[tuple[tuple[str, ...], str]] = [
    (("react", "vue", "angular", "svelte", "nextjs", "tailwind", "javascript", "typescript"), "Frontend Developer"),
    (("flask", "django", "fastapi", "express", "web app", "backend", "rest api", "graphql"), "Full Stack Web Developer"),
    (("html", "css", "frontend"), "Frontend Developer"),
    (("python", "script", "automation", "scraper", "scraping", "data pipeline"), "Python Developer"),
    (("machine learning", "neural network", "deep learning", "ml model", "tensorflow", "pytorch", "sklearn"), "Machine Learning Engineer"),
    (("sql", "database", "postgres", "mysql", "sqlite", "mongodb", "query", "schema", "migration"), "Database Engineer"),
    (("docker", "kubernetes", "k8s", "devops", "ci/cd", "deployment", "terraform", "ansible"), "DevOps Engineer"),
    (("linux", "bash", "shell", "unix", "terminal", "command line", "cron", "systemd"), "Linux Systems Engineer"),
    (("security", "pentest", "vulnerability", "exploit", "ctf", "malware", "firewall", "encryption"), "Cybersecurity Specialist"),
    (("doctor", "medical", "health", "diagnosis", "symptoms", "blood test", "patient", "medicine"), "Medical Health Advisor"),
    (("legal", "contract", "law", "attorney", "gdpr", "compliance", "terms of service"), "Legal Counsel"),
    (("marketing", "campaign", "brand", "seo", "social media", "content strategy", "advertising"), "Marketing Strategist"),
    (("finance", "stock", "trading", "investment", "portfolio", "crypto", "defi", "budget"), "Financial Analyst"),
    (("math", "algebra", "calculus", "statistics", "probability", "equation", "matrix"), "Mathematics Expert"),
    (("physics", "chemistry", "biology", "science", "quantum", "molecular", "experiment"), "Research Scientist"),
    (("write", "essay", "article", "blog", "story", "novel", "poem", "copywriting"), "Content Writer"),
    (("translate", "translation", "spanish", "french", "german", "japanese", "language"), "Language Translator"),
    (("design", "ui", "ux", "figma", "wireframe", "prototype", "user interface", "user experience"), "UI/UX Designer"),
    (("data analysis", "pandas", "numpy", "visualization", "chart", "plot", "analytics", "insights"), "Data Scientist"),
    (("android", "ios", "mobile app", "swift", "kotlin", "flutter", "react native"), "Mobile App Developer"),
    (("game", "unity", "unreal", "godot", "pygame", "game engine", "3d", "shader"), "Game Developer"),
    (("gpu", "cuda", "opencl", "hpc", "parallel computing", "simd", "compute shader"), "HPC Engineer"),
    # Broad conversational / instructional fallback — keeps API calls for true unknowns only
    (("explain", "what is", "how does", "how do", "why does", "why is", "tell me",
      "help me", "can you", "show me", "describe", "summarize", "list", "compare",
      "difference between", "what are", "give me"), "AI Assistant"),
]


# Match an explicit role assignment at the very start of the prompt:
#   "You are a senior data scientist..."     → "Senior Data Scientist"
#   "You're an expert ML engineer..."        → "Expert Ml Engineer"
#   "Act as a Python tutor..."               → "Python Tutor"
#   "Imagine you are a SQL optimiser..."     → "Sql Optimiser"
# The captured group eats up to 4 title-y words; punctuation/clauses end it.
# Matches are bounded to the first 300 chars of the prompt so that an offhand
# "your role" deeper in instructions doesn't swing the persona.
_ROLE_PROMPT_RE = re.compile(
    r"^\s*(?:you\s+are\s+(?:a|an)|you\'?re\s+(?:a|an)|"
    r"act\s+as\s+(?:a|an)|imagine\s+you\s+are\s+(?:a|an)?|"
    r"pretend\s+you\s+are\s+(?:a|an)?)\s+"
    r"([A-Za-z][A-Za-z0-9\s\-/]{2,60})",
    re.IGNORECASE,
)


def _persona_from_explicit_role(prompt: str) -> str | None:
    """If the prompt opens with an explicit role, return it as a clean
    title (Title Case, ≤ 4 words). Otherwise return None so the
    keyword / LLM heuristics run as before.

    Examples
    --------
    >>> _persona_from_explicit_role("You are a senior data scientist. Help me…")
    'Senior Data Scientist'
    >>> _persona_from_explicit_role("Act as a Python tutor and explain decorators")
    'Python Tutor'
    >>> _persona_from_explicit_role("build a flask app") is None
    True
    """
    if not prompt:
        return None
    head = prompt.lstrip()[:300]
    m = _ROLE_PROMPT_RE.match(head)
    if not m:
        return None
    raw = m.group(1).strip()
    # End the title at the first sentence boundary or clause break — common
    # separators that signal "the role ended; now an instruction starts".
    for sep in ('.', ',', ';', ':', '\n',
                ' and ', ' but ', ' so ', ' who ', ' that ',
                ' which ', ' working ', ' helping ', ' explaining ',
                ' tasked ', ' specialising ', ' specializing ', ' for '):
        if sep in raw:
            raw = raw.split(sep, 1)[0]
    raw = raw.strip(' \t\r\n.,;:!?')
    if not raw:
        return None
    # Cap at 4 words and Title Case (matches the existing persona format).
    words = raw.split()[:4]
    title = " ".join(words).title()
    return title or None


def get_dynamic_persona(prompt: str) -> str:
    """
    Select the best expert persona for this prompt.

    Order:
    1. Explicit role assignment ("You are a senior data scientist…")
       wins outright — return that title as-is.
    2. Small-talk fast path — chit-chat returns "AI Assistant".
    3. Fast path: keyword lookup (zero API calls, instant).
    4. Slow path: router model → main model fallback (only when no
       keyword matches AND a dedicated router key is configured).
    """
    # 1. Explicit-role wins — the user told us the persona; honour it.
    explicit = _persona_from_explicit_role(prompt)
    if explicit:
        return explicit

    p_lower = prompt.lower().strip()

    # Small-talk fast path — greetings and chit-chat never shapeshift.
    # Short message (≤ 8 words) that starts with or contains a small-talk token.
    if len(p_lower.split()) <= 8:
        for tok in _SMALL_TALK_TOKENS:
            if p_lower == tok or p_lower.startswith(tok + " ") or p_lower.startswith(tok + ",") \
               or p_lower.startswith(tok + "?") or p_lower.startswith(tok + "!") \
               or f" {tok} " in f" {p_lower} " or p_lower.endswith(" " + tok):
                return "AI Assistant"

    # Fast path — keyword heuristic (no API call consumed)
    for keywords, title in _PERSONA_MAP:
        if any(kw in p_lower for kw in keywords):
            return title

    # Slow path — only call the AI when a separate router is configured
    # (avoids burning main-model rate-limit quota just for persona selection)
    router_key   = get_config("router_api_key", "")
    router_url   = get_config("router_url", "")
    router_model = get_config("router_model", "")
    router_configured = bool(router_key and router_url and router_model)

    def _extract(content: str | None) -> str | None:
        if not content:
            return None
        clean = re.sub(r'[*_\"`\'#]', '', content.strip())
        clean = re.sub(
            r'^(here is|the (best )?title is|i (would )?suggest|you need|the expert is)[^a-zA-Z]*',
            '', clean, flags=re.IGNORECASE,
        )
        title = " ".join(clean.split()[:4]).strip('.:,- \n\t')
        return title if len(title) > 3 and len(title.split()) >= 2 else None

    persona_q = (
        f"User message: {prompt}\n\n"
        "What is the exact professional job title of the expert best suited to handle "
        "this request? Reply with ONLY the job title (2-4 words, no punctuation)."
    )

    if router_configured:
        try:
            resp = OpenAI(api_key=router_key, base_url=router_url).chat.completions.create(
                model=router_model,
                messages=[{"role": "user", "content": persona_q}],
                max_tokens=200, temperature=0.1,
            )
            title = _extract(resp.choices[0].message.content)
            if title:
                return title
        except Exception:
            pass

    # No router configured — fall back to main model only as last resort
    try:
        resp = OpenAI(api_key=get_api_key(), base_url=get_config("main_url")).chat.completions.create(
            model=get_config("main_model"),
            messages=[{"role": "user", "content": persona_q}],
            max_tokens=200, temperature=0.1,
        )
        title = _extract(resp.choices[0].message.content)
        if title:
            return title
    except Exception:
        pass

    return "AI Assistant"


# ─── UNIFIED API CALL ─────────────────────────────────────────────────────────

def _make_client(api_key: str) -> OpenAI:
    """Build an OpenAI-compatible client for the given key."""
    return OpenAI(api_key=api_key, base_url=get_config("main_url"))


def _call(
    client: OpenAI,
    model: str,
    messages: list,
    tools: list,
    temperature: float = 0.7,
    on_chunk=None,
    _current_key: str | None = None,
) -> tuple[str, list[dict]]:
    """
    Make an API call — streaming if on_chunk is provided, non-streaming otherwise.
    Returns (content, tool_calls).

    Rate-limit handling (free-tier resilience):
      • On 429: marks the current key as cooling, tries the next key in the pool.
      • If all keys are exhausted: waits up to _MAX_BACKOFF_S with exponential
        back-off, then retries once more before raising.
      • On empty stream: automatically retries non-streaming (model compatibility).
    """
    from omnicli.auth import get_api_key_pool
    from openai import RateLimitError

    _MAX_RETRIES   = 6
    _BASE_WAIT_S   = 5
    _MAX_BACKOFF_S = 65

    def _do_call(cl: OpenAI, key: str) -> tuple[str, list[dict]]:
        if on_chunk is not None:
            try:
                content, tc_list = _stream_and_accumulate(cl, model, messages, tools, temperature, on_chunk)
            except _StreamInterrupted as interrupted:
                # Stream died mid-flight. Tell the caller to discard the partial
                # text it already displayed, then retry once non-streaming so
                # the user gets a clean, complete response instead of garbage.
                try:
                    on_chunk("\n\n⚠️ _Stream was interrupted — retrying…_\n\n")
                except Exception:
                    pass
                console.print(
                    f"[dim red]⚠ stream interrupted after "
                    f"{len(interrupted.partial)} chars: {interrupted.cause}[/dim red]"
                )
                return _do_call_sync(cl)
            if not content.strip() and not tc_list:
                return _do_call_sync(cl)
            return content, tc_list
        return _do_call_sync(cl)

    def _do_call_sync(cl: OpenAI) -> tuple[str, list[dict]]:
        resp    = cl.chat.completions.create(model=model, messages=messages, tools=tools, temperature=temperature)
        msg     = resp.choices[0].message
        content = msg.content or ""
        tc_list = []
        if msg.tool_calls:
            tc_list = [
                {
                    "id":   tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        return content, tc_list

    pool = get_api_key_pool()

    # Use the passed-in key first; fall back to pool rotation
    active_key = _current_key or pool.get()
    if not active_key:
        active_key = get_api_key()   # absolute fallback — primary key directly

    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return _do_call(_make_client(active_key), active_key)

        except RateLimitError as e:
            pool.mark_rate_limited(active_key)
            next_key = pool.get()
            if next_key and next_key != active_key:
                # Another key is ready — switch immediately, no sleep needed
                console.print(
                    f"[dim yellow]⚑ Key {active_key[:8]}… rate-limited → rotating to key {next_key[:8]}…[/dim yellow]"
                )
                active_key = next_key
                last_err = e
                continue

            # All keys cooling — exponential back-off
            wait = min(_BASE_WAIT_S * (2 ** attempt), _MAX_BACKOFF_S)
            console.print(
                f"[dim yellow]⏳ All API keys rate-limited. Waiting {wait:.0f}s before retry "
                f"({attempt+1}/{_MAX_RETRIES})…[/dim yellow]"
            )
            import time as _t; _t.sleep(wait)
            last_err = e

            # After sleeping, grab whichever key has cooled
            refreshed = pool.get()
            if refreshed:
                active_key = refreshed

        except Exception as e:
            raise

    raise last_err or RuntimeError("All API key retries exhausted.")


class _StreamInterrupted(Exception):
    """
    Raised when the upstream stream dies mid-flight. Carries the partial text
    that made it through so the caller can decide whether to retry non-streaming
    or surface the partial result.
    """
    def __init__(self, partial: str, cause: BaseException):
        super().__init__(str(cause))
        self.partial = partial
        self.cause   = cause


def _stream_and_accumulate(
    client: OpenAI,
    model: str,
    messages: list,
    tools: list,
    temperature: float,
    on_chunk,
) -> tuple[str, list[dict]]:
    """
    Streaming call. Emits content chunks via on_chunk(text) as they arrive.
    Stops emitting chunks immediately if tool_call deltas are detected —
    in practice models don't interleave content and tool_calls.
    Returns (full_content, tool_calls_list).

    If the stream dies mid-flight (network drop, provider hiccup), we raise
    `_StreamInterrupted` carrying whatever partial content we managed to emit.
    The caller is responsible for deciding whether to retry non-streaming and
    for telling the user that the earlier fragment should be discarded.
    """
    import logging as _logging
    _log = _logging.getLogger("omnicli.engine.stream")

    stream = client.chat.completions.create(
        model=model, messages=messages, tools=tools,
        temperature=temperature, stream=True,
    )

    content_parts: list[str]     = []
    tc_map:        dict[int, dict] = {}
    saw_tool_calls = False

    try:
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                content_parts.append(delta.content)
                if not saw_tool_calls:
                    try:
                        on_chunk(delta.content)
                    except Exception:
                        # Sink errors from the on_chunk callback — losing a
                        # single frame is better than breaking the whole stream.
                        _log.debug("on_chunk callback raised", exc_info=True)

            if delta.tool_calls:
                saw_tool_calls = True
                for tc in delta.tool_calls:
                    idx = getattr(tc, 'index', 0)
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id:
                        tc_map[idx]["id"] = tc.id
                    if tc.function:
                        tc_map[idx]["name"] += tc.function.name  or ""
                        tc_map[idx]["args"] += tc.function.arguments or ""
    except Exception as e:
        partial = "".join(content_parts)
        _log.warning("stream interrupted after %d chars: %s", len(partial), e)
        raise _StreamInterrupted(partial, e) from e
    finally:
        try:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    content  = "".join(content_parts)
    tc_list  = [
        {
            "id":   tc_map[k]["id"],
            "type": "function",
            "function": {"name": tc_map[k]["name"], "arguments": tc_map[k]["args"]},
        }
        for k in sorted(tc_map.keys())
    ] if tc_map else []

    return content, tc_list


# ─── WEB SEARCH ───────────────────────────────────────────────────────────────

# Sites that reliably block headless fetchers — skip when auto-fetching
_BLOCKED_DOMAINS = {
    "espncricinfo.com", "cricbuzz.com", "reddit.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "nytimes.com", "wsj.com", "ft.com", "bloomberg.com",
}

# Keywords that signal a weather query — use tighter fetch limits for speed
_WEATHER_SIGNALS = ("weather", "temperature", "humidity", "forecast", "rain", "sunny", "climate", "wind speed")

# Keywords that signal a live/news query — supplement with Google News RSS
_NEWS_SIGNALS = (
    "today", "latest", "live", "score", "match", "ipl", "cricket", "football",
    "weather", "temperature", "news", "result", "winner", "election", "breaking",
    "price", "stock", "rate", "now", "current", "2026",
)


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lstrip("www.")
    except Exception:
        return ""


def _is_weather_query(query: str) -> bool:
    q = query.lower()
    return any(k in q for k in _WEATHER_SIGNALS)

def _is_news_query(query: str) -> bool:
    q = query.lower()
    return any(k in q for k in _NEWS_SIGNALS)

def _ddg_instant_answer(query: str) -> str:
    """
    DuckDuckGo Instant Answer API — free, no key, returns structured facts.
    Great for live sports scores, currency, weather summaries.
    """
    import requests
    from urllib.parse import quote
    try:
        r = requests.get(
            f"https://api.duckduckgo.com/?q={quote(query)}&format=json&skip_disambig=1&no_html=1",
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if not r.ok:
            return ""
        data = r.json()
        parts = []
        if data.get("Answer"):
            parts.append(f"Instant Answer: {data['Answer']}")
        if data.get("Abstract"):
            parts.append(f"Summary: {data['Abstract'][:400]}")
        if data.get("Infobox"):
            for item in data["Infobox"].get("content", [])[:6]:
                label = item.get("label", "")
                value = item.get("value", "")
                if label and value:
                    parts.append(f"{label}: {value}")
        return "\n".join(parts)
    except Exception:
        return ""


def _ddg_search(query: str, max_results: int, news: bool = False) -> list[dict]:
    """DuckDuckGo text or news search via the ddgs library."""
    try:
        from ddgs import DDGS
        ddgs = DDGS()
        if news:
            hits = list(ddgs.news(query, max_results=max_results))
            return [
                {
                    "title":   x.get("title", ""),
                    "url":     x.get("url", ""),
                    "snippet": x.get("body", x.get("excerpt", ""))[:600],
                }
                for x in hits if x.get("url")
            ]
        else:
            hits = list(ddgs.text(query, max_results=max_results))
            return [
                {
                    "title":   x.get("title", ""),
                    "url":     x.get("href", ""),
                    "snippet": x.get("body", "")[:600],
                }
                for x in hits if x.get("href")
            ]
    except Exception:
        return []


def _google_news_rss(query: str, max_results: int = 5) -> list[dict]:
    """
    Fetch Google News RSS — free, no API key, live Google-indexed news.
    Returns results with real article URLs (follows Google redirect).
    """
    import requests
    import xml.etree.ElementTree as ET
    from urllib.parse import quote

    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if not r.ok:
            return []
        root = ET.fromstring(r.content)
        results = []
        for item in root.findall(".//item")[:max_results]:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            desc  = re.sub(r"<[^>]+>", "", item.findtext("description", "")).strip()
            pub   = item.findtext("pubDate", "").strip()
            if title and link:
                snippet = f"{desc[:500]}" + (f" [{pub}]" if pub else "")
                results.append({"title": title, "url": link, "snippet": snippet[:600]})
        return results
    except Exception:
        return []


def _clean_text(raw: str, max_chars: int = 3500) -> str:
    """Strip navigation noise and return clean readable text."""
    text = re.sub(r"[ \t]+", " ", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 8:
            continue
        if re.match(r"^[\W\d\s]{0,6}$", line):
            continue
        if len(line.split()) <= 2 and len(line) < 20:
            continue
        lines.append(line)
    return "\n".join(lines)[:max_chars].strip()


def _content_quality(text: str) -> float:
    """Return 0-1 score: how much of the text is real sentences vs nav noise."""
    if not text:
        return 0.0
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return 0.0
    good = sum(1 for l in lines if len(l.split()) >= 6)
    return good / len(lines)


def _fetch_url_content(url: str, max_chars: int = 3500) -> str:
    """Fetch a URL via Jina Reader (bypasses bot blocks), fallback to requests."""
    import requests as _req
    try:
        r = _req.get(
            f"https://r.jina.ai/{url}",
            headers={
                "Accept": "text/plain",
                "X-Remove-Selector": "nav,footer,aside,script,style,header",
            },
            timeout=12,
        )
        if r.ok and len(r.text.strip()) > 200:
            return _clean_text(r.text, max_chars)
    except Exception:
        pass

    try:
        r = _req.get(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        }, timeout=10)
        if r.ok and r.text:
            from html.parser import HTMLParser
            class _S(HTMLParser):
                def __init__(self): super().__init__(); self.out = []; self._skip = False
                def handle_starttag(self, t, a):
                    if t in ("script", "style", "nav", "footer", "aside", "header"): self._skip = True
                def handle_endtag(self, t):
                    if t in ("script", "style", "nav", "footer", "aside", "header"): self._skip = False
                def handle_data(self, d):
                    if not self._skip and d.strip(): self.out.append(d.strip())
            p = _S(); p.feed(r.text)
            return _clean_text("\n".join(p.out), max_chars)
    except Exception:
        pass
    return ""


def _get_search_results(query: str, max_results: int) -> list[dict]:
    """
    Return list of {title, url, snippet}.

    Priority:
      Paid  → Brave API (if key set) → Tavily API (if key set)
      Free  → DDG text + DDG news (for live queries) + Google News RSS (for live queries)
    """
    brave_key  = get_config("brave_api_key", "")
    tavily_key = get_config("tavily_api_key", "")

    # ── Paid APIs (only if user configured them) ─────────────────────────────
    if brave_key:
        try:
            import requests
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
                params={"q": query, "count": max_results}, timeout=8,
            )
            hits = r.json().get("web", {}).get("results", [])
            if hits:
                return [{"title": x["title"], "url": x["url"], "snippet": x.get("description", "")[:600]} for x in hits]
        except Exception:
            pass

    if tavily_key:
        try:
            import requests
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": query, "max_results": max_results}, timeout=8,
            )
            hits = r.json().get("results", [])
            if hits:
                return [{"title": x["title"], "url": x["url"], "snippet": x.get("content", "")[:600]} for x in hits]
        except Exception:
            pass

    # ── Free tier ─────────────────────────────────────────────────────────────
    results: list[dict] = []
    seen_urls: set[str] = set()

    def _merge(new_hits: list[dict]) -> None:
        for h in new_hits:
            if h["url"] and h["url"] not in seen_urls:
                results.append(h)
                seen_urls.add(h["url"])

    # 1. DDG general text search (reliable, no key needed)
    _merge(_ddg_search(query, max_results))

    is_live = _is_news_query(query)

    # 2. For live/news queries: DDG news search (different index, fresher results)
    if is_live:
        _merge(_ddg_search(query, max_results=5, news=True))

    # 3. For live/news queries: Google News RSS (best live coverage, free)
    if is_live:
        _merge(_google_news_rss(query, max_results=6))

    return results[:max_results]

def _web_search(query: str, max_results: int = 8) -> str:
    """
    Search + auto-fetch: returns search snippets AND full content from the
    best reachable results (up to 3 pages), so the model has real data without
    a second tool call.
    """
    results = _get_search_results(query, max_results)
    if not results:
        return "Web search returned no results. Check internet connection."

    # Adaptive content limits — weather queries get fast shallow fetch
    is_weather = _is_weather_query(query)
    page_max_chars  = 800  if is_weather else 2000
    max_pages       = 2    if is_weather else 3
    fetch_timeout   = 8    if is_weather else 12

    output = "=== SEARCH RESULTS (snippets contain live-extracted facts) ===\n\n"

    # Prepend DDG instant answer for live/sports/weather queries
    if _is_news_query(query) or is_weather:
        instant = _ddg_instant_answer(query)
        if instant:
            output = f"=== INSTANT ANSWER ===\n{instant}\n\n" + output

    snippet_lines = [
        f"{i+1}. {r['title']}\n   URL: {r['url']}\n   {r['snippet']}"
        for i, r in enumerate(results)
    ]
    output += "\n\n".join(snippet_lines)

    # Auto-fetch: collect content from up to max_pages good pages
    _CONSENT_MARKERS = ("privacy promise", "cookie consent", "we respect your privacy",
                        "before you continue", "sign in to continue", "enable javascript",
                        "gdpr", "accept cookies", "access to this page has been denied")
    pages_fetched = 0
    for r in results:
        if pages_fetched >= max_pages:
            break
        if _domain(r["url"]) in _BLOCKED_DOMAINS:
            continue
        content = _fetch_url_content(r["url"], max_chars=page_max_chars)
        if not content:
            continue
        if any(m in content[:500].lower() for m in _CONSENT_MARKERS):
            continue
        if _content_quality(content) < 0.25:
            continue
        output += f"\n\n=== PAGE CONTENT #{pages_fetched+1} (source: {r['url']}) ===\n\n{content}"
        pages_fetched += 1

    return output


# ─── PROJECT CONTEXT ─────────────────────────────────────────────────────────

def _get_project_context(cwd: str) -> str:
    """Detect project type + key files from CWD and return a summary string."""
    import json as _json
    lines: list[str] = []

    # Node.js
    pkg_path = os.path.join(cwd, "package.json")
    if os.path.exists(pkg_path):
        try:
            pkg = _json.loads(open(pkg_path).read(4000))
            name = pkg.get("name", "?"); ver = pkg.get("version", "?")
            scripts = list(pkg.get("scripts", {}).keys())[:5]
            deps    = list(pkg.get("dependencies", {}).keys())[:8]
            lines.append(f"Node.js project: {name} v{ver}")
            if scripts: lines.append(f"  scripts: {', '.join(scripts)}")
            if deps:    lines.append(f"  dependencies: {', '.join(deps)}")
        except Exception:
            lines.append("Node.js project detected (package.json)")

    # Python
    for pf in ("pyproject.toml", "setup.py", "setup.cfg"):
        if os.path.exists(os.path.join(cwd, pf)):
            lines.append(f"Python project ({pf})")
            break
    req_path = os.path.join(cwd, "requirements.txt")
    if os.path.exists(req_path):
        try:
            reqs = open(req_path).read(1000).splitlines()
            top  = [r.split("==")[0].split(">=")[0].strip()
                    for r in reqs if r.strip() and not r.startswith("#")][:6]
            if top: lines.append(f"  requirements: {', '.join(top)}")
        except Exception:
            pass

    # Project instruction files
    for md in ("CLAUDE.md", ".phantom.md", "PHANTOM.md", ".cursorrules"):
        md_path = os.path.join(cwd, md)
        if os.path.exists(md_path):
            try:
                lines.append(f"\n{md} (project instructions):\n{open(md_path).read(2000)}")
            except Exception:
                pass

    # Top-level file listing
    try:
        entries = sorted([e for e in os.listdir(cwd) if not e.startswith(".")])[:20]
        if entries: lines.append(f"  dir contents: {', '.join(entries)}")
    except Exception:
        pass

    return "\n".join(lines)


def _get_git_context(cwd: str) -> str:
    """Return git branch, status and recent log (if inside a repo)."""
    import subprocess as _sp
    if not os.path.exists(os.path.join(cwd, ".git")):
        return ""
    lines: list[str] = []
    try:
        b = _sp.run(["git", "branch", "--show-current"],
                    capture_output=True, text=True, timeout=5, cwd=cwd)
        if b.returncode == 0 and b.stdout.strip():
            lines.append(f"Git branch: {b.stdout.strip()}")
    except Exception:
        pass
    try:
        s = _sp.run(["git", "status", "--short"],
                    capture_output=True, text=True, timeout=5, cwd=cwd)
        if s.returncode == 0 and s.stdout.strip():
            lines.append(f"Git status:\n{s.stdout.strip()[:500]}")
    except Exception:
        pass
    try:
        lg = _sp.run(["git", "log", "--oneline", "-5"],
                     capture_output=True, text=True, timeout=5, cwd=cwd)
        if lg.returncode == 0 and lg.stdout.strip():
            lines.append(f"Recent commits:\n{lg.stdout.strip()[:300]}")
    except Exception:
        pass
    return "\n".join(lines)


def _trim_history(messages: list, max_context_chars: int = 24_000) -> list:
    """
    Trim conversation history to stay within a character budget.
    Always keeps the system prompt intact. Drops whole turns (user + following
    assistant/tool exchanges) from oldest first — never cuts mid-turn, which
    would produce orphan tool messages that break the OpenAI API.
    """
    system = [m for m in messages if m.get("role") == "system"]
    others = [m for m in messages if m.get("role") != "system"]

    # Group into turns: each turn starts at a user message.
    # Leading non-user messages (e.g. pre-chat assistant messages) form turn 0.
    turns: list[list] = []
    current: list = []
    for m in others:
        if m.get("role") == "user" and current:
            turns.append(current)
            current = [m]
        else:
            current.append(m)
    if current:
        turns.append(current)

    sys_chars = sum(len(m.get("content") or "") for m in system)
    budget    = max_context_chars - sys_chars

    # Drop oldest turns until everything fits
    while turns:
        total = sum(len(m.get("content") or "") for t in turns for m in t)
        if total <= budget:
            break
        turns.pop(0)   # drop oldest complete turn

    return system + [m for t in turns for m in t]


# ─── AUDIT LOG ────────────────────────────────────────────────────────────────

_AUDIT_LOG   = os.path.expanduser("~/.omnicli/.audit.log")
_write_undo_stack: list[tuple[str, str | None]] = []   # (path, old_content_or_None)


def _audit_write(path: str, ok: bool, reason: str = "") -> None:
    import json as _json, time as _t
    try:
        entry = _json.dumps({"ts": _t.time(), "tool": "write_file", "path": path, "ok": ok, "why": reason})
        with open(_AUDIT_LOG, "a") as f:
            f.write(entry + "\n")
        if os.name != "nt":
            os.chmod(_AUDIT_LOG, 0o600)
    except Exception:
        pass


# ─── FILE READ TOOL ───────────────────────────────────────────────────────────

def _read_file(path: str, trust: int) -> str:
    """Read a file from the local filesystem and return its contents."""
    _MAX_READ = 32_000   # chars returned to model

    if trust < 1:
        return "Error: read_file requires trust level 1 or higher."
    if not path:
        return "Error: path is required."

    try:
        home = str(Path.home())
        path = path.replace("~", home)
        p = Path(path)
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.exists():
            return f"Error: File not found: {p}"
        if p.is_dir():
            # For directories, return a listing
            entries = sorted(p.iterdir())
            lines = [f"Directory: {p}", ""]
            for e in entries[:200]:
                size = f"  ({e.stat().st_size:,} bytes)" if e.is_file() else "/"
                lines.append(f"  {'📁' if e.is_dir() else '📄'} {e.name}{size}")
            if len(entries) > 200:
                lines.append(f"  … and {len(entries)-200} more")
            return "\n".join(lines)
        if not p.is_file():
            return f"Error: Not a regular file: {p}"
        size = p.stat().st_size
        if size > 500_000:
            return f"Error: File too large to read ({size:,} bytes). Max 500KB."
        content = p.read_text(encoding="utf-8", errors="replace")
        lines   = len(content.splitlines())
        if len(content) > _MAX_READ:
            content = content[:_MAX_READ] + f"\n\n[... truncated — showing first {_MAX_READ:,} chars of {len(content):,} total ...]"
        return f"=== FILE: {p}  ({lines} lines · {size:,} bytes) ===\n\n{content}"
    except PermissionError:
        return f"Error: Permission denied reading {path}"
    except Exception as e:
        return f"Error reading file: {e}"


# ─── FILE WRITE TOOL ──────────────────────────────────────────────────────────

_ALLOWED_WRITE_ROOTS = None   # populated lazily


def _get_allowed_roots() -> tuple[str, ...]:
    global _ALLOWED_WRITE_ROOTS
    if _ALLOWED_WRITE_ROOTS is None:
        home = str(Path.home())
        tmp  = tempfile.gettempdir()   # /tmp on Linux/Mac, %TEMP% on Windows
        _ALLOWED_WRITE_ROOTS = (home, tmp)
    return _ALLOWED_WRITE_ROOTS


def _write_file(path: str, content: str, trust: int) -> str:
    """Write content to a file on the local filesystem."""
    if trust < 2:
        _audit_write(path, False, "trust < 2")
        return "Error: write_file requires trust level 2 or higher."
    if not path:
        return "Error: path is required."

    try:
        home = str(Path.home())
        path = path.replace("~", home)
        for wrong in ("/home/user/", "/home/ubuntu/", "/home/phantom/", "/home/admin/"):
            if path.startswith(wrong):
                path = home + "/" + path[len(wrong):]
                break
        p = Path(path)
        if not p.is_absolute():
            p = Path.cwd() / p

        # Path restriction: must be under $HOME or /tmp unless God Mode
        if trust < 4:
            allowed = _get_allowed_roots()
            if not any(str(p).startswith(r) for r in allowed):
                _audit_write(str(p), False, f"path outside allowed roots: {allowed}")
                return (
                    f"Error: write_file blocked — path '{p}' is outside allowed directories.\n"
                    f"Allowed: {allowed}\n"
                    f"Use a path under your home directory or /tmp."
                )

        # Save previous content for /undo
        old = None
        if p.exists():
            try:
                old = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                old = None
        _write_undo_stack.append((str(p), old))
        if len(_write_undo_stack) > 10:
            _write_undo_stack.pop(0)

        p.parent.mkdir(parents=True, exist_ok=True)

        # Auto-repair double-escaped content. The model occasionally emits
        # write_file content with literal `\n`, `\t`, `\"` instead of real
        # newlines/tabs/quotes — produces a single-line .py that crashes with
        # `SyntaxError: unexpected character after line continuation character`.
        # Detection: file is source-like AND content has very few real newlines
        # AND many literal `\n` two-char sequences. If so, decode them.
        if str(p).endswith((".py", ".js", ".ts", ".sh", ".bat", ".html", ".css", ".json")):
            real_nl   = content.count("\n")
            literal_nl = content.count("\\n")
            if literal_nl >= 5 and literal_nl > real_nl * 3 and len(content) > 80:
                try:
                    repaired = (content
                                .replace("\\n", "\n")
                                .replace("\\t", "\t")
                                .replace("\\r", "\r")
                                .replace('\\"', '"')
                                .replace("\\'", "'")
                                .replace("\\\\", "\\"))
                    # For .py files, only accept the repair if it now parses.
                    if str(p).endswith(".py"):
                        import ast as _ast
                        _ast.parse(repaired)
                    content = repaired
                except (SyntaxError, ValueError):
                    pass  # Repair didn't help; keep original and let caller retry

        p.write_text(content, encoding="utf-8")
        lines = len(content.splitlines())
        size  = len(content.encode())
        _audit_write(str(p), True)
        return (
            f"✓ File written: {p}\n"
            f"  {lines} lines · {size:,} bytes\n"
            f"  Run with: python {p}" if str(p).endswith(".py") else
            f"✓ File written: {p}\n  {lines} lines · {size:,} bytes"
        )
    except PermissionError:
        _audit_write(path, False, "permission denied")
        return f"Error: Permission denied writing to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


# ─── FILE EDIT (PATCH) TOOL ───────────────────────────────────────────────────

def _edit_file(path: str, old_text: str, new_text: str, trust: int) -> str:
    """
    Patch a file by replacing old_text with new_text (first occurrence).
    Safer than write_file for targeted edits — no need to resend the whole file.
    """
    if trust < 2:
        return "Error: edit_file requires trust level 2 or higher."
    if not path:
        return "Error: path is required."
    if old_text is None:
        return "Error: old_text is required."

    try:
        home = str(Path.home())
        path = path.replace("~", home)
        p = Path(path)
        if not p.is_absolute():
            p = Path.cwd() / p

        if trust < 4:
            allowed = _get_allowed_roots()
            if not any(str(p).startswith(r) for r in allowed):
                return f"Error: edit_file blocked — '{p}' is outside allowed directories."

        if not p.exists():
            return f"Error: File not found: {p}\nUse write_file to create new files."

        content = p.read_text(encoding="utf-8", errors="replace")

        # Normalise line endings for matching
        if old_text not in content:
            cn = content.replace('\r\n', '\n')
            on = old_text.replace('\r\n', '\n')
            if on not in cn:
                return (
                    f"Error: The text to replace was not found verbatim in {p}.\n"
                    "Tip: Use read_file first to copy the exact text, then call edit_file."
                )
            content, old_text = cn, on

        # Save old content for /undo
        _write_undo_stack.append((str(p), content))
        if len(_write_undo_stack) > 10:
            _write_undo_stack.pop(0)

        new_content = content.replace(old_text, new_text, 1)
        p.write_text(new_content, encoding="utf-8")

        old_l = old_text.count('\n') + 1
        new_l = new_text.count('\n') + 1
        _audit_write(str(p), True, f"edit_file: {old_l}→{new_l} lines")
        return (
            f"✓ File patched: {p}\n"
            f"  Replaced {old_l} line(s) with {new_l} line(s)\n"
            f"  Total: {len(new_content.splitlines())} lines · {len(new_content.encode()):,} bytes"
        )
    except PermissionError:
        _audit_write(path, False, "permission denied")
        return f"Error: Permission denied editing {path}"
    except Exception as e:
        return f"Error editing file: {e}"


# ─── TOOL EXECUTOR ────────────────────────────────────────────────────────────

def _tool_display_name(name: str, args: dict) -> str:
    if name == "run_bash":
        cmd = (args.get("command") or "").strip().splitlines()[0] if args.get("command") else ""
        return f"Run: {cmd[:60]}" if cmd else "Run shell"
    if name == "browse_url":
        return f"Browse {args.get('url', '')[:60]}"
    if name == "web_search":
        return f"Search: {args.get('query', '')[:60]}"
    if name == "write_file":
        return f"Write {args.get('path', '')}"
    if name == "read_file":
        return f"Read {args.get('path', '')}"
    if name == "edit_file":
        return f"Edit {args.get('path', '')}"
    if name == "plan_tasks":
        return "Plan tasks"
    return name


def _execute_tool(name: str, args: dict, trust: int, on_bash_output=None, tracker: "TaskTracker | None" = None) -> str:
    # ── Schema validation BEFORE dispatch ────────────────────────────────
    # Without this, a malformed model output (missing key, wrong type) would
    # silently call the underlying function with defaults ("" for strings,
    # empty list, etc.), producing confusing downstream errors. With this,
    # the model receives a structured error it can retry against.
    try:
        from omnicli.tool_schemas import validate as _validate_args
        ok, err = _validate_args(name, args if isinstance(args, dict) else {})
        if not ok:
            log.warning("tool arg validation failed for %s: %s", name, err)
            return err
    except ImportError:
        pass

    # ── PreToolUse hook ──────────────────────────────────────────────────
    # Shell-command hook can veto the call. Matches Claude Code's model:
    # non-zero exit blocks, exit 0 allows. Errors fail-open (hook bugs
    # shouldn't brick the CLI).
    try:
        from omnicli.hooks import dispatch as _hook_dispatch, is_configured as _hooks_configured
        if _hooks_configured():
            r = _hook_dispatch("PreToolUse", {"tool": name, "args": args})
            if not r.allowed:
                log.warning("PreToolUse hook blocked %s: %s", name, r.reason)
                msg = f"HOOK_BLOCKED({name}): {r.reason}"
                if r.stderr:
                    msg += f"\nhook stderr: {r.stderr[:500]}"
                return msg
    except ImportError:
        pass
    except Exception as _he:
        log.warning("PreToolUse dispatch error (fail-open): %s", _he)

    task_id = None
    if tracker is not None and name != "plan_tasks":
        task_id = tracker.add(_tool_display_name(name, args), status="running")
    try:
        if name == "run_bash":
            out = execute_bash(args.get("command", ""), trust, on_output=on_bash_output)
        elif name == "browse_url":
            out = run_browser(args.get("url", ""))
        elif name == "web_search":
            out = _web_search(args.get("query", ""), int(args.get("max_results", 8) or 8))
        elif name == "write_file":
            out = _write_file(args.get("path", ""), args.get("content", ""), trust)
        elif name == "read_file":
            out = _read_file(args.get("path", ""), trust)
        elif name == "edit_file":
            out = _edit_file(args.get("path", ""), args.get("old_text", ""), args.get("new_text", ""), trust)
        elif name == "plan_tasks":
            items = args.get("tasks") or args.get("steps") or []
            if isinstance(items, str):
                items = [s.strip() for s in items.splitlines() if s.strip()]
            items = [str(x).strip() for x in items if str(x).strip()][:20]
            if tracker is not None and items:
                tracker.plan(items)
            out = f"Planned {len(items)} task(s)." if items else "No tasks provided."
        else:
            out = f"Unknown tool: {name}"
    except Exception as e:
        if task_id is not None and tracker is not None:
            tracker.finish(task_id, ok=False, detail=str(e)[:120])
        raise
    if task_id is not None and tracker is not None:
        ok = not (isinstance(out, str) and out.startswith(("Error:", "⚠")))
        detail = out.strip().splitlines()[0][:120] if isinstance(out, str) and out.strip() else ""
        tracker.finish(task_id, ok=ok, detail=detail)

    # ── PostToolUse hook (informational, errors swallowed) ──────────────
    try:
        from omnicli.hooks import dispatch as _hook_dispatch, is_configured as _hooks_configured
        if _hooks_configured():
            try:
                _hook_dispatch("PostToolUse", {
                    "tool":   name,
                    "args":   args,
                    "output": out if isinstance(out, str) else str(out),
                })
            except Exception as _pe:
                log.debug("PostToolUse dispatch error (ignored): %s", _pe)
    except ImportError:
        pass
    return out


# ─── INTENT DETECTION ────────────────────────────────────────────────────────

_FILE_INTENT_KEYWORDS = (
    "write", "create", "generate", "make", "build", "save", "output",
    "app.py", ".py", ".html", ".js", ".ts", ".json", ".yaml", ".yml",
    ".sh", ".txt", ".md", ".css", ".sql", "script", "program",
    "flask", "django", "fastapi", "express", "react", "dashboard",
    "web app", "webapp", "backend", "frontend", "api server",
)

def _wants_file_output(prompt: str) -> bool:
    """Return True when the user prompt implies code/file creation."""
    p = prompt.lower()
    return any(kw in p for kw in _FILE_INTENT_KEYWORDS)


_CODE_BLOCK_RE = re.compile(r"```[\w]*\n(.+?)```", re.DOTALL)


def _extract_code_blocks(text: str) -> list[str]:
    """Return a list of code block bodies from markdown text."""
    return [m.group(1).strip() for m in _CODE_BLOCK_RE.finditer(text) if m.group(1).strip()]


# ─── MAIN RESPONSE GENERATOR ──────────────────────────────────────────────────

def generate_response(
    prompt: str,
    chat_history: list,
    trust_level: int,
    on_chunk=None,
    tracker: "TaskTracker | None" = None,
    persona: str | None = None,
) -> tuple[str, list]:
    """
    Generate a response for the given prompt.

    on_chunk: optional callable(str) — called with each streamed text delta.
              When provided, the final answer streams to the caller in real time.
              Tool execution phases are always synchronous regardless.
    tracker:  optional TaskTracker — every tool call and plan_tasks invocation
              updates the tracker so the caller can render live progress.
    """
    from omnicli.auth import get_api_key_pool
    from omnicli.memory import search_rag_memory
    _pool       = get_api_key_pool()
    _active_key = _pool.get() or get_api_key()
    main_client = _make_client(_active_key)
    main_model  = get_config("main_model")
    expert_title = persona or get_dynamic_persona(prompt)

    _home = str(Path.home())
    _cwd  = os.getcwd()

    # ── Context enrichment (RAG + project + git) ────────────────────────────
    _rag_hits = search_rag_memory(prompt, limit=3)
    _rag_ctx  = ("\n\nRELEVANT MEMORY (long-term facts you stored):\n"
                 + "\n".join(f"• {h}" for h in _rag_hits)) if _rag_hits else ""

    _proj_ctx = _get_project_context(_cwd)
    _proj_section = f"\n\nPROJECT CONTEXT (CWD: {_cwd}):\n{_proj_ctx}" if _proj_ctx else ""

    # ── Active multi-agent project context ────────────────────────────────
    # Set by _run_multi_agent / AgentOrchestrator after a successful build.
    # Without this, the model loses the project dir between turns and
    # `cd`'s into paths that don't exist in its fresh shell.
    def _sys_os_for_debug() -> str:
        return (get_config("sys_os", "") or "").strip() or "Linux"

    _last_proj_dir    = (get_config("last_project_dir", "")    or "").strip()
    _last_proj_entry  = (get_config("last_project_entry", "")  or "").strip()
    _last_proj_prompt = (get_config("last_project_prompt", "") or "").strip()
    _active_project_section = ""
    if _last_proj_dir and os.path.isdir(_last_proj_dir):
        run_hint = ""
        if _last_proj_entry and _last_proj_entry.endswith(".py"):
            run_hint = f"python \"{_last_proj_entry}\""
        elif _last_proj_entry and _last_proj_entry.endswith(".js"):
            run_hint = f"node \"{_last_proj_entry}\""

        # Running-app debug context — present only if Phantom auto-launched
        # the app and we have its PID + log file + URL on disk.
        _last_app_pid = (get_config("last_app_pid", "") or "").strip()
        _last_app_log = (get_config("last_app_log", "") or "").strip()
        _last_app_url = (get_config("last_app_url", "") or "").strip()
        _debug_block = ""
        if _last_app_log and os.path.isfile(_last_app_log):
            _is_win = _sys_os_for_debug() == "Windows"
            _diag_ps1 = (get_config("last_app_diag_ps1", "") or "").strip()
            _diag_sh  = (get_config("last_app_diag_sh",  "") or "").strip()
            # Single self-contained script handles: PID-alive check, HTTP hit
            # with body capture, log tail — with proper error handling for
            # connection-refused / null-response / missing curl. No inline
            # PowerShell or curl flags in the prompt to break across OSes.
            if _is_win and _diag_ps1:
                _diag_cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{_diag_ps1}"'
            elif _diag_sh:
                _diag_cmd = f'bash "{_diag_sh}"'
            else:
                _diag_cmd = ""
            _debug_block = (
                "\n\nRUNNING APP (auto-launched by Phantom):\n"
                + (f"  PID     : {_last_app_pid}\n" if _last_app_pid else "")
                + (f"  URL     : {_last_app_url}\n" if _last_app_url else "")
                + f"  LOG FILE: {_last_app_log}\n"
                + (f"  DIAG    : {_diag_cmd}\n" if _diag_cmd else "")
                + "\n"
                + "DEBUG WORKFLOW — when the user reports 'internal server error', "
                  "'link not working', '500 error', 'page broken', 'fix the bug', or "
                  "any equivalent complaint about the running app:\n"
                + "  STEP 1 — RUN THE DIAGNOSTIC SCRIPT (ONCE). It returns process-alive "
                  "status, HTTP status + response body (with full FastAPI/Flask traceback "
                  "if any), and the last 100 log lines — everything you need:\n"
                + (f"           run_bash: {_diag_cmd}\n" if _diag_cmd else "")
                + "  STEP 2 — READ THE OUTPUT and pick exactly ONE next move:\n"
                + "           a) PROCESS DEAD → app crashed. The log tail will contain a "
                  "Python traceback ending in `File \"...\", line N, in ...`. Open THAT file at THAT line, fix it, restart.\n"
                + "           b) HTTP STATUS 500 + body has traceback → open the file/line "
                  "named in the traceback, fix it, restart.\n"
                + "           c) HTTP STATUS 200 → the app is fine; ask the user what they "
                  "actually saw (wrong content? blank page? specific error in browser?).\n"
                + "           d) CONNECTION_ERROR + process ALIVE → port mismatch or app "
                  "is bound to wrong interface; check the bind in the runner/entry file.\n"
                + "           e) Output is empty/inconclusive → tell the user what you "
                  "saw and ASK before changing code. Do NOT start reading source files.\n"
                + "  STEP 3 — After editing, restart the app: kill the old PID, then\n"
                + (f"           re-run the runner from {_last_proj_dir}.\n" if _last_proj_dir else "")
                + "  STEP 4 — Re-run the diagnostic script (STEP 1) to verify status is "
                  "now 200, then tell the user what you fixed.\n"
                + "HARD RULES:\n"
                + "  • Do NOT read source files BEFORE STEP 1. The diag output tells you "
                  "which file to read.\n"
                + "  • Do NOT call STEP 1 more than twice (once to diagnose, once to verify).\n"
                + "  • Do NOT inline PowerShell or curl flags — just call the DIAG line above.\n"
                + "Diagnose first, fix second."
            )

        _active_project_section = (
            f"\n\nACTIVE PROJECT (set by previous multi-agent build):\n"
            f"  DIR     : {_last_proj_dir}\n"
            + (f"  ENTRY   : {_last_proj_entry}\n" if _last_proj_entry else "")
            + (f"  PROMPT  : {_last_proj_prompt}\n" if _last_proj_prompt else "")
            + (f"  RUN CMD : {run_hint}\n" if run_hint else "")
            + "When the user says 'run the app', 'run it', 'start the server', "
              "'share the link', or any equivalent without naming a project — "
              "this is the project they mean. Always use the absolute paths above; "
              "do NOT rely on `cd` persisting between run_bash calls (each call is a fresh shell). "
              "Prefer `python \"<absolute-entry>\"` over `cd <dir> && python entry`."
            + _debug_block
        )

    _git_ctx = _get_git_context(_cwd)
    _git_section = f"\n\nGIT CONTEXT:\n{_git_ctx}" if _git_ctx else ""

    # Owner + system context from DB
    _bot_name    = get_config("bot_name",      "PhantomCLI")
    _owner_name  = get_config("owner_name",    "")
    _owner_role  = get_config("owner_role",    "")
    _owner_domain= get_config("owner_domain",  "")
    _sys_os      = get_config("sys_os",        "Linux")
    _sys_distro  = get_config("sys_distro",    "")
    _sys_arch    = get_config("sys_arch",      "x86_64")
    _sys_ram     = get_config("sys_ram_gb",    "")
    _sys_cpu     = get_config("sys_cpu_cores", "")

    _owner_ctx = ""
    if _owner_name:
        _owner_ctx = f"OPERATOR: {_owner_name}"
        if _owner_role:   _owner_ctx += f" · {_owner_role}"
        if _owner_domain: _owner_ctx += f" · Domain: {_owner_domain}"
        _owner_ctx += "\n"

    _os_ctx = f"HOST OS: {_sys_distro or _sys_os} {_sys_arch}"
    if _sys_ram:  _os_ctx += f"  RAM: {_sys_ram}GB"
    if _sys_cpu:  _os_ctx += f"  CPU cores: {_sys_cpu}"
    _os_ctx += f"  HOME: {_home}  CWD: {_cwd}"

    _tmp_dir   = tempfile.gettempdir()
    _shell_ctx = (
        f"Shell: {_SHELL_NAME}. "
        + ("Use PowerShell or CMD syntax for shell commands." if _IS_WINDOWS
           else "Use bash/POSIX syntax for shell commands.")
    )

    sys_prompt = (
        f"You are {_bot_name}, a God-Mode AI OS running on {_sys_os}, shapeshifted into a {expert_title}.\n"
        f"{_owner_ctx}"
        f"{_os_ctx}\n"
        f"{_shell_ctx}\n"
        f"You have FULL access to the host machine via: 'run_bash', 'web_search', 'browse_url', 'write_file', 'read_file', and 'edit_file'.\n"
        f"IMPORTANT: Always use HOME={_home} for file paths. Never guess platform-specific home dirs.\n\n"
        f"CRITICAL RULES:\n"
        f"1. FILE CREATION RULE: Whenever you generate code, scripts, HTML, configs, or any file content — "
        f"you MUST call 'write_file' to save it directly to disk. NEVER just show code in a markdown code block "
        f"without ALSO calling write_file. Choose a sensible path under HOME={_home} or {_tmp_dir}. "
        f"If the task needs multiple files, write ALL of them with separate write_file calls. "
        f"After writing every file, confirm each path and show how to run it.\n"
        f"2. READ BEFORE EDITING RULE: If the user asks you to fix, edit, or improve an existing file, "
        f"ALWAYS call 'read_file' first to see the current contents. Never assume what a file contains.\n"
        f"3. FOR LIVE/CURRENT INFO (news, scores, weather, prices, etc): ALWAYS use 'web_search' first.\n"
        f"   The tool returns: (a) INSTANT ANSWERS — structured live facts, (b) SEARCH SNIPPETS — live data from pages, "
        f"   (c) PAGE CONTENT from up to 3 sources. All are real verified facts — extract everything.\n"
        f"4. SNIPPET RULE: If a snippet or instant answer says 'KKR 147/6 in 20 overs', report it exactly. "
        f"NEVER say 'not available' if the data is already in the tool output.\n"
        f"5. QUERY STRATEGY: Be specific. For sports: team names + date + year. For weather: city + 'today'. "
        f"Run a second targeted search if first result lacks detail.\n"
        f"6. NEVER invent live data. Only report what's in tool output.\n"
        f"7. EFFICIENCY: Combine shell operations. Complete tasks fully. Format with headers/tables/bullets.\n"
        f"8. EDIT vs WRITE: To change a specific section of an existing file, prefer 'edit_file' over 'write_file'. "
        f"Use edit_file when you know the exact text to replace — it's faster and safer.\n"
        f"9. PROGRESS VISIBILITY: If the request clearly needs multiple tool calls (e.g. build an app, refactor, "
        f"research-then-write), call 'plan_tasks' ONCE at the very start with a short ordered checklist "
        f"(2–8 items, each under 60 chars) so the user sees live progress. Skip for trivial one-step requests.\n"
        f"10. RUN WEB SERVER RECIPE: When the user says 'run it' / 'start the server' / "
        f"'share the link' for a Flask/FastAPI/Express app, follow this EXACT sequence:\n"
        f"   (a) cd into the project dir (run_bash).\n"
        f"   (b) Install deps: `pip install -r requirements.txt` (or `npm install`). "
        f"Always do this BEFORE starting the app — don't assume the venv already has them.\n"
        f"   (c) Start the server in the background. "
        + (f"PowerShell: `Start-Process -FilePath python -ArgumentList 'app.py' -WindowStyle Hidden -PassThru` "
           if _IS_WINDOWS else
           f"POSIX: `nohup python app.py > /tmp/phantom_app.log 2>&1 & echo $!` ") +
        f"— capture the PID.\n"
        f"   (d) Wait ~2 seconds: "
        + (f"PowerShell: `Start-Sleep -Seconds 2`. " if _IS_WINDOWS else f"POSIX: `sleep 2`. ") +
        f"\n"
        f"   (e) Probe the ROOT url `http://127.0.0.1:<port>/` — NOT `/api/...`. "
        + (f"PowerShell: `try {{ (Invoke-WebRequest -Uri http://127.0.0.1:5000/ -UseBasicParsing -TimeoutSec 5).StatusCode }} catch {{ $_.Exception.Message }}` " if _IS_WINDOWS else
           f"POSIX: `curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:5000/`") +
        f"\n"
        f"   (f) ANY HTTP response (200, 302, 404 on /) means the server bound successfully. "
        f"Only a connection refused / timeout means it failed.\n"
        f"   (g) On ModuleNotFoundError in (c): pip install the missing package, then retry from (c). "
        f"Also append the package to requirements.txt so the next run works.\n"
        f"   (h) Report EXACTLY ONE line to the user: "
        f"`Server running → http://127.0.0.1:<port>/  (PID: <pid>)`. "
        f"Never paste HTML bodies, stack traces from successful runs, or probe output. "
        f"If it crashed, report the traceback's last 3 lines and stop.\n"
        f"   (i) Do NOT use bash heredoc (`python - <<PY ... PY`) on Windows — that's bash-only. "
        + (f"On Windows use `python -c \"...\"` with a single-quoted inline script, or write a "
           f".py file and call `python file.py`." if _IS_WINDOWS else "") +
        f"\n"
        f"11. NEVER output raw JSON plan objects to the user. If you have a plan, execute it "
        f"via the plan_tasks / write_file / run_bash tools — do not print the JSON itself. "
        f"A reply that starts with {{ \"task\": or {{ \"steps\": is a bug, not an answer.\n"
        f"12. DATA / ML WORKFLOWS: When the user asks for data analysis, ML models, dashboards, or reports — "
        f"treat it as a full build. Steps you typically take: "
        f"(a) Inspect the dataset (`read_file` for CSV/JSON, or `run_bash` with `head`, `wc -l`, `file`); "
        f"(b) Pip-install required libraries via `run_bash` (`pip install --quiet pandas scikit-learn matplotlib plotly fastapi uvicorn streamlit seaborn` etc); "
        f"(c) Write full Python files with `write_file` — data loader, preprocessing, training, evaluation, visualisation — "
        f"split into modular files under a project dir (e.g. {_home}/phantom_projects/<task-slug>/); "
        f"(d) Run the pipeline with `run_bash` and capture metrics/plots; "
        f"(e) Build an interactive dashboard on top — FastAPI+Jinja/HTMX, Flask, or Streamlit — serve locally and print the URL; "
        f"(f) Write a concise report (markdown) summarising features, model choice, metrics, and how to run. "
        f"Pick libraries by task: pandas/polars for tabular, scikit-learn for classical ML, xgboost/lightgbm for gradient boosting, "
        f"pytorch/tensorflow for deep learning, transformers for NLP, statsmodels for stats, "
        f"matplotlib/seaborn/plotly for viz, fastapi/flask/streamlit for dashboards. "
        f"Never stop after just 'here is the code' — actually run the training and serve the dashboard."
        f"{_rag_ctx}{_proj_section}{_git_section}{_active_project_section}"
    )

    # Build message list; trim history to stay within context budget
    _history_msgs = [{"role": m["role"], "content": m["content"]} for m in chat_history]
    _history_msgs = _trim_history(
        [{"role": "system", "content": sys_prompt}] + _history_msgs
    )
    messages: list = _history_msgs
    messages.append({"role": "user", "content": prompt})

    tools = [
        {
            "type": "function",
            "function": {
                "name":        "run_bash",
                "description": f"Execute a shell command ({_SHELL_NAME}). Returns stdout/stderr.",
                "parameters":  {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name":        "browse_url",
                "description": "Launch headless browser to fetch full text content of a specific URL.",
                "parameters":  {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name":        "web_search",
                "description": "Search the web for current information (news, scores, prices, etc.). Returns titles, URLs, and snippets. Use this BEFORE browse_url to find the right page.",
                "parameters":  {
                    "type": "object",
                    "properties": {
                        "query":       {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "description": "Number of results (default 8)"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name":        "write_file",
                "description": (
                    "Write text content to a file on the local filesystem. "
                    "Use this to save generated code, configs, HTML, scripts, or any output directly to disk "
                    "so the user can run it immediately. Always write complete, runnable files."
                ),
                "parameters":  {
                    "type": "object",
                    "properties": {
                        "path":    {"type": "string", "description": "Absolute or relative file path (e.g. 'app.py', '~/projects/dashboard.html')"},
                        "content": {"type": "string", "description": "Full file content to write"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name":        "read_file",
                "description": (
                    "Read the contents of an existing file or list directory contents. "
                    "Use this BEFORE editing any file to see what's already there. "
                    "Also use it to inspect code, configs, logs, or any text file on disk."
                ),
                "parameters":  {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative path to file or directory"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name":        "edit_file",
                "description": (
                    "Patch an existing file by replacing a specific block of text with new text. "
                    "Preferred over write_file when making targeted edits — no need to resend the whole file. "
                    "Use read_file first to get the exact text to replace."
                ),
                "parameters":  {
                    "type": "object",
                    "properties": {
                        "path":     {"type": "string", "description": "Absolute or relative path to the file"},
                        "old_text": {"type": "string", "description": "Exact text block to find and replace (verbatim, including indentation and newlines)"},
                        "new_text": {"type": "string", "description": "New text to replace old_text with"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "plan_tasks",
                "description": (
                    "Declare a short checklist of sub-tasks you are about to perform. "
                    "Call this ONCE at the very start when the user's request will require "
                    "multiple tool calls, so the user can see real-time progress. "
                    "Keep each item under 60 chars. 2–8 items is ideal. "
                    "Do not call this for trivial one-step requests."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Ordered list of short step descriptions.",
                        },
                    },
                    "required": ["tasks"],
                },
            },
        },
    ]

    if expert_title != "AI Assistant":
        console.print(
            f"[dim italic]PhantomCLI shapeshifted to: {expert_title.upper()} "
            f"(Powered by {main_model})[/dim italic]"
        )

    try:
        _wants_file = _wants_file_output(prompt)
        _forced_write_attempted = False
        _json_retry_done = False
        content, tc_list = _call(main_client, main_model, messages, tools, 0.7, on_chunk, _current_key=_active_key)
        tool_outputs: list[dict] = []

        def _make_follow_up(used_tools: set[str], this_round_is_search: bool, this_round_is_file: bool) -> str:
            wrote_file_ever = any(t["tool"] == "write_file" for t in tool_outputs)
            if this_round_is_search and not this_round_is_file:
                base = (
                    "Search complete. Extract EVERY fact from the tool output — read all snippets, "
                    "INSTANT ANSWER, and PAGE CONTENT sections. Snippets and instant answers contain "
                    "live scores, temperatures, prices — treat them as confirmed facts. "
                    "Report all numbers, names, and stats. Do NOT say 'not available' if the data is present. "
                    "Do NOT add training knowledge."
                )
                if _wants_file and not wrote_file_ever:
                    return (
                        base + "\n\nIMPORTANT: The user also wants a FILE created. "
                        "After summarizing the search results, call write_file NOW to save the output "
                        f"to a sensible path under {_home}/ — write the complete file content, then confirm the path."
                    )
                return base + " Write a complete answer with source URLs."
            if this_round_is_file:
                return (
                    "Tool executed. If there are MORE files to write or commands to run to complete the task, "
                    "call write_file or run_bash now — do NOT stop early. "
                    "Only write your final summary text AFTER all files have been written to disk. "
                    "For each file written, confirm the path and show how to run it."
                )
            return (
                "Tool complete. If more steps are needed, call the next tool now. "
                "Otherwise write a complete, well-formatted final answer."
            )

        for _round in range(_max_rounds()):

            # ── Structured tool calls ─────────────────────────────────────────
            if tc_list:
                messages.append({
                    "role":       "assistant",
                    "content":    content or None,
                    "tool_calls": tc_list,
                })
                _round_tools: set[str] = set()
                for tc in tc_list:
                    name   = tc["function"]["name"]
                    args   = json.loads(tc["function"]["arguments"])
                    output = _execute_tool(name, args, trust_level, on_bash_output=on_chunk, tracker=tracker)
                    tool_outputs.append({"tool": name, "output": output})
                    _round_tools.add(name)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})
                _is_search_round = bool(_round_tools & {"web_search", "browse_url"})
                _is_file_round   = bool(_round_tools & {"write_file", "run_bash"})
                _follow_up = _make_follow_up(_round_tools, _is_search_round, _is_file_round)
                messages.append({"role": "user", "content": _follow_up})
                content, tc_list = _call(main_client, main_model, messages, tools, 0.7, on_chunk, _current_key=_active_key)

            # ── Text-based tool calls ─────────────────────────────────────────
            elif text_calls := _parse_text_tool_calls(content):
                clean = _strip_tool_calls(content)
                _round_tools = set()
                for tc in text_calls:
                    tc_id  = f"txt-{uuid.uuid4().hex[:8]}"
                    name   = tc["name"]
                    args   = tc["args"]
                    output = _execute_tool(name, args, trust_level, on_bash_output=on_chunk, tracker=tracker)
                    tool_outputs.append({"tool": name, "output": output})
                    _round_tools.add(name)
                    messages.append({
                        "role":    "assistant",
                        "content": clean or None,
                        "tool_calls": [{
                            "id":   tc_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }],
                    })
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": output})
                _is_search_round = bool(_round_tools & {"web_search", "browse_url"})
                _is_file_round   = bool(_round_tools & {"write_file", "run_bash"})
                _follow_up = _make_follow_up(_round_tools, _is_search_round, _is_file_round)
                messages.append({"role": "user", "content": _follow_up})
                content, tc_list = _call(main_client, main_model, messages, tools, 0.7, on_chunk, _current_key=_active_key)

            # ── Final answer ──────────────────────────────────────────────────
            else:
                # Raw planner JSON leak guard — model sometimes emits a bare
                # {"task": [...], "steps": [...]} object instead of calling
                # plan_tasks/write_file. Re-prompt once to execute the plan.
                _stripped = _strip_tool_calls(content).strip()
                _looks_like_planner_json = (
                    _stripped.startswith("{")
                    and _stripped.endswith("}")
                    and any(k in _stripped[:200] for k in ('"task"', '"steps"', '"tasks"', '"plan"'))
                )
                if _looks_like_planner_json and not _json_retry_done:
                    try:
                        json.loads(_stripped)   # confirm it really is JSON
                        messages.append({"role": "assistant", "content": content})
                        messages.append({
                            "role": "user",
                            "content": (
                                "That JSON plan is not a user-facing answer. "
                                "EXECUTE the plan now: call write_file / run_bash / edit_file "
                                "for each step, then write a short human-readable summary of what you did. "
                                "Do NOT output the plan JSON again."
                            ),
                        })
                        content, tc_list = _call(main_client, main_model, messages, tools, 0.7, on_chunk, _current_key=_active_key)
                        _json_retry_done = True
                        continue
                    except (json.JSONDecodeError, ValueError):
                        pass

                # If prompt wanted a file but model only put code in a text block, force write_file
                wrote_file = any(t["tool"] == "write_file" for t in tool_outputs)
                if _wants_file and not wrote_file and not _forced_write_attempted:
                    code_blocks = _extract_code_blocks(content)
                    if code_blocks:
                        _forced_write_attempted = True
                        # Detect likely filename from content or prompt
                        _fname = "app.py"
                        for ext in (".html", ".js", ".ts", ".sh", ".yaml", ".yml", ".json", ".sql", ".css"):
                            if ext in content.lower() or ext in prompt.lower():
                                _fname = f"app{ext}"
                                break
                        messages.append({"role": "assistant", "content": content})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"You wrote the code but did NOT call write_file. "
                                f"Call write_file NOW to save it to {_home}/{_fname} "
                                f"(or a more appropriate path). "
                                f"Write the COMPLETE file content — do not truncate. "
                                f"Then confirm the path and how to run it."
                            ),
                        })
                        content, tc_list = _call(main_client, main_model, messages, tools, 0.7, on_chunk, _current_key=_active_key)
                        # Allow one more round for the forced write_file call
                        continue
                break

        # Post-process
        final = _strip_tool_calls(content).strip()
        if not final:
            if tool_outputs:
                # Tools ran but the model gave no wrap-up text. Surface their
                # outputs with an honest label so the user sees what happened.
                _LABELS = {
                    "run_bash":   "Terminal Output",
                    "read_file":  "File Contents",
                    "write_file": "File Written",
                    "edit_file":  "File Patched",
                    "web_search": "Web Result",
                    "search_memory": "Memory Search",
                }
                parts = []
                for t in tool_outputs:
                    out = (t.get("output") or "").strip()
                    if not out:
                        continue
                    label = _LABELS.get(t.get("tool", ""), "Tool Output")
                    body = out[:3000]
                    if t.get("tool") == "run_bash":
                        parts.append(f"**{label}:**\n```\n{body}\n```")
                    else:
                        parts.append(f"**{label}:**\n{body}")
                final = "\n\n".join(parts) if parts else "Command executed but returned no output."
            else:
                # No tools ran — model returned empty content. Likely a model/API issue.
                final = "The model returned an empty response. This may be a temporary issue with the configured model. Try again or switch models with `/model <name>`."

        return final, messages

    except Exception as e:
        return f"**API Error:** {str(e)}", messages
