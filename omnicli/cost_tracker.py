"""
Cost + spend tracking — per-session accumulator and cross-session history.

A model call reports token usage (prompt tokens, completion tokens,
cached tokens). We multiply by the per-model price sheet to compute USD
spend, aggregate it, and expose it via the /cost slash command + via a
Notification hook that fires when a daily spend threshold is crossed.

Storage:
  ~/.phantom/spend.jsonl   — one line per model call {ts, model, p_tok,
                             c_tok, cached_tok, usd}

API:
  record(model, prompt_tokens, completion_tokens, cached_tokens=0)
  total_today() / total_session() / history(days=7)
  session_summary() → SessionSummary
  price_for(model) → PriceEntry
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("omnicli.cost_tracker")


# ─── Price sheet ─────────────────────────────────────────────────────────────
# USD per 1M tokens. Update as public rates change.

@dataclass(frozen=True)
class PriceEntry:
    model:              str
    input_per_million:  float
    output_per_million: float
    cached_per_million: float = 0.0       # Anthropic cache read; OpenAI cached rates similar
    vendor:             str = ""


_PRICES: dict[str, PriceEntry] = {
    # ── Anthropic ────────────────────────────────────────────────────────────
    "claude-opus-4-5":        PriceEntry("claude-opus-4-5",       15.0, 75.0, 1.5,  "anthropic"),
    "claude-opus-4-7":        PriceEntry("claude-opus-4-7",       15.0, 75.0, 1.5,  "anthropic"),
    "claude-sonnet-4-5":      PriceEntry("claude-sonnet-4-5",      3.0, 15.0, 0.3,  "anthropic"),
    "claude-sonnet-4-6":      PriceEntry("claude-sonnet-4-6",      3.0, 15.0, 0.3,  "anthropic"),
    "claude-haiku-4-5":       PriceEntry("claude-haiku-4-5",       0.8,  4.0, 0.08, "anthropic"),
    # ── OpenAI ───────────────────────────────────────────────────────────────
    "gpt-4o":                 PriceEntry("gpt-4o",                 2.5, 10.0, 1.25, "openai"),
    "gpt-4o-mini":            PriceEntry("gpt-4o-mini",            0.15, 0.6, 0.075,"openai"),
    "gpt-4.1":                PriceEntry("gpt-4.1",                2.0, 8.0,  0.5,  "openai"),
    # ── Groq (Llama family, heavily subsidized) ──────────────────────────────
    "llama-3.3-70b-versatile":   PriceEntry("llama-3.3-70b-versatile",   0.59, 0.79, 0.0, "groq"),
    "llama-3.1-8b-instant":      PriceEntry("llama-3.1-8b-instant",     0.05, 0.08, 0.0, "groq"),
    # ── NVIDIA NIM (typical public) ──────────────────────────────────────────
    "meta/llama-3.3-70b-instruct": PriceEntry("meta/llama-3.3-70b-instruct", 0.4, 0.4, 0.0, "nvidia"),
}


_DEFAULT_PRICE = PriceEntry(model="(unknown)", input_per_million=2.0,
                            output_per_million=8.0, cached_per_million=0.0)


def price_for(model: str) -> PriceEntry:
    """Look up the price entry; fall back to a safe default estimate."""
    if not model:
        return _DEFAULT_PRICE
    # Exact match
    if model in _PRICES:
        return _PRICES[model]
    # Loose match: some providers prefix the model id with a vendor path
    low = model.lower()
    for k, v in _PRICES.items():
        if low.endswith(k.lower()) or low.startswith(k.lower()):
            return v
    return _DEFAULT_PRICE


def register_price(entry: PriceEntry) -> None:
    """Add or override a price entry (e.g. for custom deployments)."""
    _PRICES[entry.model] = entry


# ─── Cost math ───────────────────────────────────────────────────────────────


def compute_usd(
    model: str,
    prompt_tokens:      int,
    completion_tokens:  int,
    cached_tokens:      int = 0,
) -> float:
    p = price_for(model)
    # Cached tokens are discounted — they replace prompt tokens
    regular_prompt = max(0, prompt_tokens - cached_tokens)
    cost = (
        regular_prompt     * p.input_per_million  / 1_000_000
        + completion_tokens * p.output_per_million / 1_000_000
        + cached_tokens     * p.cached_per_million / 1_000_000
    )
    return round(cost, 6)


# ─── Session + history ───────────────────────────────────────────────────────


@dataclass
class SessionSummary:
    calls:     int   = 0
    prompt_tokens:     int = 0
    completion_tokens: int = 0
    cached_tokens:     int = 0
    usd:       float = 0.0
    by_model:  dict[str, dict[str, float]] = field(default_factory=dict)


def _spend_log_path() -> str:
    return os.environ.get(
        "PHANTOM_SPEND_LOG",
        os.path.expanduser("~/.phantom/spend.jsonl"),
    )


_lock = threading.Lock()
_session: SessionSummary = SessionSummary()
_alert_threshold_usd = float(os.environ.get("PHANTOM_DAILY_ALERT_USD", "10.0"))
_alerted_today: set[str] = set()   # dates we already alerted on


def reset_session() -> None:
    """Zero the in-memory session counters (does NOT touch the on-disk log)."""
    global _session
    with _lock:
        _session = SessionSummary()
    _alerted_today.clear()


def session_summary() -> SessionSummary:
    with _lock:
        return _session


def record(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Record one model call. Returns the USD cost of the call."""
    usd = compute_usd(model, prompt_tokens, completion_tokens, cached_tokens)
    ts = time.time()
    _append_log({
        "ts":      ts,
        "iso":     datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds"),
        "model":   model,
        "p_tok":   prompt_tokens,
        "c_tok":   completion_tokens,
        "cached":  cached_tokens,
        "usd":     usd,
    })
    with _lock:
        _session.calls += 1
        _session.prompt_tokens     += prompt_tokens
        _session.completion_tokens += completion_tokens
        _session.cached_tokens     += cached_tokens
        _session.usd               += usd
        entry = _session.by_model.setdefault(model, {"calls": 0, "usd": 0.0,
                                                     "p_tok": 0, "c_tok": 0})
        entry["calls"] += 1
        entry["usd"]   += usd
        entry["p_tok"] += prompt_tokens
        entry["c_tok"] += completion_tokens
    _maybe_alert_daily_threshold()
    return usd


def _append_log(entry: dict) -> None:
    path = _spend_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.warning("could not write spend log: %s", e)


def history(days: int = 7) -> list[dict]:
    """Return every logged entry from the last `days` days, newest-first."""
    path = _spend_log_path()
    if not os.path.isfile(path):
        return []
    cutoff = time.time() - days * 86400
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("ts", 0) < cutoff:
                    continue
                rows.append(r)
    except OSError:
        return []
    rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return rows


def total_today() -> float:
    rows = history(days=1)
    today = datetime.now(timezone.utc).date().isoformat()
    return round(sum(r.get("usd", 0.0) for r in rows
                     if r.get("iso", "").startswith(today)), 4)


def total_session() -> float:
    return round(session_summary().usd, 4)


# ─── Notification hook integration ───────────────────────────────────────────


def _maybe_alert_daily_threshold() -> None:
    """Fire a Notification hook when today's spend first crosses the threshold."""
    today = datetime.now(timezone.utc).date().isoformat()
    if today in _alerted_today:
        return
    spent = total_today()
    if spent < _alert_threshold_usd:
        return
    try:
        from omnicli.hooks import dispatch as _hook_dispatch, is_configured
        if is_configured():
            _hook_dispatch("Notification", {
                "level":     "warn",
                "msg":       f"Daily Phantom spend crossed ${spent:.2f} (threshold ${_alert_threshold_usd:.2f}).",
                "category":  "spend",
                "amount_usd": spent,
            })
    except Exception as e:
        log.debug("daily-alert hook dispatch failed: %s", e)
    _alerted_today.add(today)


def set_daily_alert_threshold(usd: float) -> None:
    global _alert_threshold_usd
    _alert_threshold_usd = float(usd)
    _alerted_today.clear()


__all__ = [
    "price_for", "register_price", "PriceEntry",
    "compute_usd", "record", "history",
    "total_today", "total_session", "session_summary",
    "reset_session", "set_daily_alert_threshold",
    "SessionSummary",
]
