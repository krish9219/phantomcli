"""
LLM-backed summariser for context compaction.

The default `_default_summariser` in `context_compact.py` is deterministic
and lossy — it just emits role counts + first/last user turns. For long
sessions that's not enough. This module wraps an OpenAI-compatible client
call to produce a proper conversation summary the model can still reason
against.

Design:
  * `summarise(messages, client=None)` → str
  * Retries with exponential backoff on transient network errors
  * Caches by content hash so repeat compactions of the same middle
    window are free
  * Falls back to the deterministic summariser on hard failure rather
    than raising (never break the agent loop)

Caller supplies the `client` (so tests can inject mocks). In production
the engine passes its configured OpenAI client.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from functools import lru_cache
from typing import Any, Callable, Optional

from omnicli.context_compact import _default_summariser

log = logging.getLogger("omnicli.llm_summarizer")

_SYSTEM_PROMPT = (
    "You are a summarization assistant. The following messages are the "
    "middle of a longer AI-agent conversation. Summarise them tightly "
    "so the main agent can still reason about what happened without "
    "re-reading the transcript.\n\n"
    "Cover:\n"
    "  • what the user asked (goals, constraints, any explicit style preferences)\n"
    "  • what tools were called, with the KEY arguments and WHAT THEY RETURNED "
    "    (file paths written, commands run, errors hit)\n"
    "  • any decisions the agent made (architectural choices, library picks)\n"
    "  • any open threads / things deferred\n"
    "Be terse. Use bullet points. Output plain text, no markdown fences. "
    "Max 500 words. Do not invent details not present in the transcript."
)


# ─── Caching ─────────────────────────────────────────────────────────────────


def _hash_messages(msgs: list[dict]) -> str:
    """Stable hash of a message list for cache keys."""
    try:
        blob = json.dumps(msgs, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(msgs)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


_SUMMARY_CACHE: dict[str, str] = {}
_CACHE_MAX = 64


def _cache_get(k: str) -> Optional[str]:
    return _SUMMARY_CACHE.get(k)


def _cache_put(k: str, v: str) -> None:
    if len(_SUMMARY_CACHE) >= _CACHE_MAX:
        # Evict an arbitrary old entry
        _SUMMARY_CACHE.pop(next(iter(_SUMMARY_CACHE)))
    _SUMMARY_CACHE[k] = v


def clear_cache() -> None:
    _SUMMARY_CACHE.clear()


# ─── Public API ──────────────────────────────────────────────────────────────


def summarise(
    messages: list[dict],
    client:   Optional[Any] = None,
    model:    Optional[str] = None,
    max_retries: int = 3,
) -> str:
    """Return a summary string. Never raises — falls back to the
    deterministic summariser on any hard failure.

    `client` is an OpenAI-compatible client with .chat.completions.create.
    `model` defaults to the config's main_model. If `client` is None we
    try to build one from config + env; if that fails we fall back."""
    if not messages:
        return ""
    key = _hash_messages(messages)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    if client is None:
        client = _default_client()
        if client is None:
            log.info("no LLM client configured — using deterministic summariser")
            return _default_summariser(messages)

    if model is None:
        model = _default_model() or "gpt-4o-mini"

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            text = _call_llm(client, model, messages)
            if text:
                _cache_put(key, text)
                return text
        except Exception as e:  # transient network / API errors
            last_err = e
            delay = min(2 ** attempt, 8)
            log.warning("summariser attempt %d failed (%s) — retrying in %ds",
                        attempt + 1, e, delay)
            time.sleep(delay)
    log.warning("summariser gave up after %d retries: %s — falling back",
                max_retries, last_err)
    return _default_summariser(messages)


def make_callable(client: Any = None, model: Optional[str] = None) -> Callable[[list[dict]], str]:
    """Return a summariser function compatible with `context_compact.compact(summariser=...)`."""
    def _fn(messages: list[dict]) -> str:
        return summarise(messages, client=client, model=model)
    return _fn


# ─── Internals ───────────────────────────────────────────────────────────────


def _call_llm(client: Any, model: str, messages: list[dict]) -> str:
    # Build a fresh message list: our system prompt + the original messages as
    # a structured payload (JSON-encoded so the model sees roles clearly).
    payload = json.dumps(messages, default=str)[:180_000]
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": f"Summarise this transcript:\n{payload}"},
        ],
        max_tokens=1200,
        temperature=0.2,
    )
    try:
        return (resp.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, KeyError):
        # Defensive — if the client/response shape is exotic, fall back.
        return ""


def _default_client() -> Optional[Any]:
    try:
        from openai import OpenAI
        from omnicli.memory import get_config
        from omnicli.auth import get_api_key
        key = get_api_key()
        if not key:
            return None
        return OpenAI(api_key=key, base_url=get_config("main_url", None))
    except Exception:
        return None


def _default_model() -> Optional[str]:
    try:
        from omnicli.memory import get_config
        return get_config("main_model", "") or None
    except Exception:
        return None


__all__ = ["summarise", "make_callable", "clear_cache"]
