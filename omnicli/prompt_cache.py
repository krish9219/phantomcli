"""
Prompt cache annotation — Anthropic-style `cache_control` for stable
message parts.

Anthropic's API lets you mark message content blocks as cacheable:

    {"type": "text", "text": "long system prompt", "cache_control": {"type": "ephemeral"}}

Subsequent calls with the same cached prefix read from cache, cutting
latency and cost by ~80%+. Phantom previously sent every message as a
plain string with no cache annotation, so every turn paid the full
system-prompt tokens.

This module:
  1. Detects which messages are WORTH caching (stable across turns and
     over a minimum token threshold — cache hits have a break-even of
     ~1024 tokens for the ephemeral tier).
  2. Converts them from string content to the Anthropic structured form
     with `cache_control: {"type": "ephemeral"}`.
  3. Exposes a toggle: OpenAI-compatible providers ignore the flag
     (extra fields are tolerated in most), Anthropic honors it.

Two flavors:
  * `annotate_system(messages, min_tokens=1024)` — marks the contiguous
    system-role block at the front.
  * `annotate_long_blocks(messages, min_tokens=4096)` — marks any
    individual message whose estimated tokens exceed the threshold
    (used for CLAUDE.md context or long file reads).

API is side-effect-free: returns a new list. Safe to call on every turn.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Literal

from omnicli.context_compact import count_tokens

log = logging.getLogger("omnicli.prompt_cache")

CacheType = Literal["ephemeral", "persistent"]

# Anthropic's break-even threshold. Caching a block smaller than this
# actually costs a tiny bit more than not caching. Tune via env.
DEFAULT_MIN_SYSTEM_TOKENS = 1024
DEFAULT_MIN_BLOCK_TOKENS  = 4096


def _mark(text: str, cache_type: CacheType = "ephemeral") -> dict:
    """Wrap a string as an Anthropic content block with cache_control."""
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": cache_type},
    }


def _ensure_list_content(content: Any) -> list[dict]:
    """Coerce a string-or-list content into a list of blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return list(content)
    return [{"type": "text", "text": str(content)}]


# ─── System-block annotation ─────────────────────────────────────────────────


def annotate_system(
    messages: list[dict],
    min_tokens: int = DEFAULT_MIN_SYSTEM_TOKENS,
    cache_type: CacheType = "ephemeral",
) -> list[dict]:
    """Return a NEW message list with the contiguous leading system-role
    block marked for caching (if total tokens >= min_tokens).

    Multiple system messages are concatenated into one cache block so we
    get a single cache hit rather than N."""
    if not messages:
        return list(messages)

    # Collect leading system messages
    leading_idx = []
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            leading_idx.append(i)
        else:
            break
    if not leading_idx:
        return list(messages)

    total = sum(count_tokens(messages[i]) for i in leading_idx)
    if total < min_tokens:
        return list(messages)

    # Build merged block. Preserve each original message's content but merge
    # into a single blob so there's one cache write.
    merged_parts: list[dict] = []
    for i in leading_idx:
        for block in _ensure_list_content(messages[i].get("content", "")):
            merged_parts.append(block)
    # Put cache_control on the LAST block of the system prefix — Anthropic
    # caches up to and including the marked block.
    if merged_parts:
        merged_parts[-1] = {
            **merged_parts[-1],
            "cache_control": {"type": cache_type},
        }

    out: list[dict] = []
    out.append({"role": "system", "content": merged_parts})
    out.extend(messages[leading_idx[-1] + 1:])
    return out


# ─── Long-block annotation ───────────────────────────────────────────────────


def annotate_long_blocks(
    messages: list[dict],
    min_tokens: int = DEFAULT_MIN_BLOCK_TOKENS,
    cache_type: CacheType = "ephemeral",
    skip_roles: Iterable[str] = ("assistant",),
) -> list[dict]:
    """Mark any individual message over `min_tokens` for caching.

    Skips assistant-role messages by default — those typically aren't
    repeated verbatim across turns so caching doesn't pay off.
    """
    skip = set(skip_roles)
    out: list[dict] = []
    for m in messages:
        role = m.get("role", "")
        if role in skip:
            out.append(m)
            continue
        if count_tokens(m) < min_tokens:
            out.append(m)
            continue
        blocks = _ensure_list_content(m.get("content", ""))
        if not blocks:
            out.append(m)
            continue
        blocks = [dict(b) for b in blocks]
        blocks[-1] = {**blocks[-1], "cache_control": {"type": cache_type}}
        out.append({**m, "content": blocks})
    return out


# ─── Audit / introspection ───────────────────────────────────────────────────


def cached_block_count(messages: list[dict]) -> int:
    """How many cache_control markers are present in the message list."""
    n = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("cache_control"):
                    n += 1
    return n


def strip_cache_controls(messages: list[dict]) -> list[dict]:
    """Return a NEW list with every cache_control key removed. Used when
    sending to a provider that rejects unknown fields."""
    out = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            clean = []
            for b in content:
                if isinstance(b, dict):
                    bb = {k: v for k, v in b.items() if k != "cache_control"}
                    clean.append(bb)
                else:
                    clean.append(b)
            out.append({**m, "content": clean})
        else:
            out.append(m)
    return out


__all__ = [
    "annotate_system",
    "annotate_long_blocks",
    "cached_block_count",
    "strip_cache_controls",
    "DEFAULT_MIN_SYSTEM_TOKENS",
    "DEFAULT_MIN_BLOCK_TOKENS",
]
