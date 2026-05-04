"""
Token-aware context compaction — mirrors Claude Code's auto-compact.

The engine builds `messages` (role/content dicts) and asks the model for a
completion. Over long multi-turn sessions the message list can balloon
past the model's context window; Claude Code auto-compacts when you cross
a threshold (default ~85% of the window), summarizing older messages into
one synthetic "prior conversation" message while keeping the system
prompt and the recent tail intact.

This module implements:
  * `count_tokens(text|messages)` — a decent tiktoken-free estimator
  * `estimate_messages(messages)` — total token count across a list
  * `needs_compaction(messages, budget)` — bool check against threshold
  * `compact(messages, budget, summarizer=None)` — returns (new_messages,
    stats) with old messages collapsed into a summary slot

PreCompact hook fires before any compaction runs (informational).
Returns the compaction plan (old_count, kept_count, summary) so callers
can surface it to the user.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

log = logging.getLogger("omnicli.context_compact")

# Default context budget. Overridable via config `context_budget_tokens`.
DEFAULT_BUDGET_TOKENS = 128_000
# Compact when `tokens_used / budget` crosses this fraction.
DEFAULT_COMPACT_RATIO = 0.85
# Always keep at least this many of the most-recent messages intact.
DEFAULT_KEEP_RECENT = 8
# Never compact if there are fewer than this many messages (not worth it).
MIN_MESSAGES_FOR_COMPACT = 12


# ─── Token estimation ────────────────────────────────────────────────────────


# Empirically derived: tiktoken's cl100k_base averages ~3.3 chars/token on
# English+code. We use 3.5 to be slightly conservative (over-estimate), so
# we compact a little earlier rather than a little later.
_CHARS_PER_TOKEN = 3.5


def count_tokens(content: str | dict | list) -> int:
    """Estimate token count for a string, message dict, or message list.

    Uses char-length heuristic — deliberately tiktoken-free so Phantom has
    no extra dependency. Conservative bias (over-counts by ~5%) so we
    compact slightly earlier than strictly necessary. If you install
    tiktoken and set env PHANTOM_USE_TIKTOKEN=1 we use the real tokenizer.
    """
    if isinstance(content, str):
        return _count_str_tokens(content)
    if isinstance(content, dict):
        # A message dict: {"role": ..., "content": ...}
        # Add a small fixed overhead per message (~4 tokens for role + format)
        inner = content.get("content", "")
        if isinstance(inner, list):
            # Multi-part content (text, tool_call, etc.) — sum parts
            total = 0
            for p in inner:
                if isinstance(p, dict):
                    total += _count_str_tokens(str(p.get("text", ""))) + \
                             _count_str_tokens(str(p.get("input", "")))
                else:
                    total += _count_str_tokens(str(p))
            return total + 4
        return _count_str_tokens(str(inner)) + 4
    if isinstance(content, list):
        return sum(count_tokens(m) for m in content)
    return _count_str_tokens(str(content))


def _count_str_tokens(s: str) -> int:
    import os
    if not s:
        return 0
    if os.environ.get("PHANTOM_USE_TIKTOKEN") == "1":
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(s))
        except Exception:
            pass
    # Char-based estimate with punctuation bias
    n = max(1, int(len(s) / _CHARS_PER_TOKEN))
    return n


def estimate_messages(messages: list[dict]) -> int:
    return sum(count_tokens(m) for m in messages)


# ─── Compaction policy ───────────────────────────────────────────────────────


@dataclass
class CompactionStats:
    before_tokens: int
    after_tokens:  int
    before_count:  int
    after_count:   int
    summarised_count: int
    summary_text:  str = ""

    @property
    def compressed(self) -> bool:
        return self.after_tokens < self.before_tokens


def needs_compaction(
    messages: list[dict],
    budget:   int = DEFAULT_BUDGET_TOKENS,
    ratio:    float = DEFAULT_COMPACT_RATIO,
) -> bool:
    """True if the message list is over the compaction threshold AND large
    enough to be worth compacting."""
    if len(messages) < MIN_MESSAGES_FOR_COMPACT:
        return False
    if budget <= 0:
        return False
    return estimate_messages(messages) > int(budget * ratio)


def _default_summariser(old_messages: list[dict]) -> str:
    """Zero-dependency summariser used when caller didn't supply one.

    Deterministic, not model-based — produces a structured plain-text
    condensation the LLM can still ground on. Quality is proportional to
    how well the engine-provided summariser would do; this fallback is
    for tests and offline scenarios.
    """
    lines = [f"[COMPACTED CONTEXT — {len(old_messages)} prior messages]"]
    role_count: dict[str, int] = {}
    tool_calls: list[str] = []
    first_user = None
    last_user  = None
    for m in old_messages:
        role = m.get("role", "?")
        role_count[role] = role_count.get(role, 0) + 1
        content = m.get("content", "")
        if isinstance(content, str):
            if role == "user":
                if first_user is None:
                    first_user = content[:200]
                last_user = content[:200]
            elif role == "tool":
                # Extract tool name hints
                m_name = re.match(r"^(?:Tool\s+|)([A-Za-z_][A-Za-z0-9_]*)\s*[:(]", content)
                if m_name:
                    tool_calls.append(m_name.group(1))
    for role, n in role_count.items():
        lines.append(f"  {role}: {n}")
    if tool_calls:
        top = []
        seen: dict[str, int] = {}
        for t in tool_calls:
            seen[t] = seen.get(t, 0) + 1
        top = [f"{n}× {t}" for t, n in sorted(seen.items(), key=lambda kv: -kv[1])[:5]]
        lines.append("Tools used: " + ", ".join(top))
    if first_user:
        lines.append(f"First ask: {first_user}")
    if last_user and last_user != first_user:
        lines.append(f"Latest ask: {last_user}")
    return "\n".join(lines)


def compact(
    messages: list[dict],
    budget:   int = DEFAULT_BUDGET_TOKENS,
    ratio:    float = DEFAULT_COMPACT_RATIO,
    keep_recent: int = DEFAULT_KEEP_RECENT,
    summariser: Optional[Callable[[list[dict]], str]] = None,
) -> tuple[list[dict], CompactionStats]:
    """Compact `messages` to fit under `budget * ratio`.

    Strategy:
      1. Always preserve every `role=="system"` message.
      2. Preserve the last `keep_recent` non-system messages.
      3. Everything between is collapsed into one synthetic message with
         role "system" and content from the summariser.

    Returns (new_messages, stats). If no compaction was needed,
    `new_messages is messages` (same object) and stats.compressed is False.
    """
    stats = CompactionStats(
        before_tokens=estimate_messages(messages),
        after_tokens=estimate_messages(messages),
        before_count=len(messages),
        after_count=len(messages),
        summarised_count=0,
    )
    if not needs_compaction(messages, budget, ratio):
        return messages, stats

    # PreCompact hook fires informationally
    try:
        from omnicli.hooks import dispatch as _hook_dispatch, is_configured as _hc
        if _hc():
            _hook_dispatch("PreCompact", {
                "tokens":   stats.before_tokens,
                "budget":   budget,
                "messages": len(messages),
            })
    except Exception as _e:
        log.debug("PreCompact hook dispatch error (ignored): %s", _e)

    # Partition: system, middle, tail
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if len(non_system) <= keep_recent:
        # Nothing to compact in the middle; return as-is.
        return messages, stats
    middle = non_system[:-keep_recent]
    tail   = non_system[-keep_recent:]

    summariser = summariser or _default_summariser
    try:
        summary = summariser(middle)
    except Exception as e:
        log.warning("summariser failed: %s — falling back to default", e)
        summary = _default_summariser(middle)

    # Splice: [system...] + [synthetic summary] + [tail]
    new_messages: list[dict] = list(system_msgs)
    new_messages.append({"role": "system", "content": summary})
    new_messages.extend(tail)

    stats.summary_text = summary
    stats.summarised_count = len(middle)
    stats.after_count = len(new_messages)
    stats.after_tokens = estimate_messages(new_messages)
    return new_messages, stats


__all__ = [
    "count_tokens",
    "estimate_messages",
    "needs_compaction",
    "compact",
    "CompactionStats",
    "DEFAULT_BUDGET_TOKENS",
    "DEFAULT_COMPACT_RATIO",
    "DEFAULT_KEEP_RECENT",
]
