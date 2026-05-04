"""
Message-list preparation pipeline for a model call.

Today `engine.generate_response` builds `messages` inline — concatenating
history, system prompt, persona, user turn. This module consolidates
that final prep step so ALL providers see a consistent, cache-optimized,
context-enriched payload:

  1. Inject auto-discovered CLAUDE.md / CONTEXT.md content (user +
     project + local scopes)
  2. Apply Anthropic cache_control to the stable system prefix
  3. Mark any single very-long user block as cacheable
  4. Compact if the list is approaching the context budget

Providers that ignore `cache_control` (OpenAI, Groq, NVIDIA) tolerate the
extra field — it passes through as an unknown-properties block and is
silently dropped server-side. So we can always annotate, and only the
Anthropic path gets the benefit.

The engine's caller supplies `provider` so we can decide whether to
strip cache_control before sending.
"""
from __future__ import annotations

import logging
from typing import Callable, Literal, Optional

log = logging.getLogger("omnicli.prompt_builder")

Provider = Literal["anthropic", "openai", "groq", "nvidia", "gemini", "auto"]


def build(
    messages:  list[dict],
    provider:  Provider = "auto",
    project_dir:    Optional[str] = None,
    compact_budget: int = 128_000,
    inject_context: bool = True,
    apply_cache:    bool = True,
    summariser:     Optional[Callable[[list[dict]], str]] = None,
) -> list[dict]:
    """Return the final message list to send to a provider.

    Applies (in order):
      1. CONTEXT.md hierarchy injection (via context_memory.load)
      2. Context compaction if over `compact_budget`
      3. System-prefix cache_control annotation (provider=anthropic)
      4. Large-user-block cache annotation

    If `provider` is "openai"/"groq"/"nvidia"/"gemini", cache markers are
    stripped at the end — those providers reject unknown fields less
    gracefully than Anthropic's lenient policy. If "auto" we leave markers
    in place (Anthropic honours them, others ignore or soft-reject).
    """
    out = list(messages)

    # 1. Context hierarchy injection
    if inject_context:
        try:
            from omnicli.context_memory import load, inject_into_messages
            merged = load(start=project_dir)
            if not merged.empty:
                out = inject_into_messages(out, merged)
        except Exception as e:
            log.debug("context memory inject skipped: %s", e)

    # 2. Compaction
    try:
        from omnicli.context_compact import needs_compaction, compact
        if needs_compaction(out, budget=compact_budget):
            out, stats = compact(out, budget=compact_budget, summariser=summariser)
            log.info("context compacted: %d→%d messages, %d→%d tokens",
                     stats.before_count, stats.after_count,
                     stats.before_tokens, stats.after_tokens)
    except Exception as e:
        log.debug("compaction skipped: %s", e)

    # 3+4. Cache annotation
    if apply_cache:
        try:
            from omnicli.prompt_cache import (
                annotate_system, annotate_long_blocks, strip_cache_controls,
            )
            out = annotate_system(out)
            out = annotate_long_blocks(out)
            # Providers that shouldn't see cache_control: strip before send
            if provider in ("openai", "groq", "nvidia", "gemini"):
                out = strip_cache_controls(out)
        except Exception as e:
            log.debug("cache annotation skipped: %s", e)

    return out


def detect_provider(base_url: str = "") -> Provider:
    """Classify a base_url into a known provider bucket. Used by the engine
    so callers don't have to pass `provider` explicitly."""
    u = (base_url or "").lower()
    if "anthropic" in u:
        return "anthropic"
    if "groq" in u:
        return "groq"
    if "nvidia" in u or "build.nvidia" in u:
        return "nvidia"
    if "googleapis" in u or "generativelanguage" in u:
        return "gemini"
    if "openai" in u or "api.deepseek" in u or "api.mistral" in u:
        return "openai"
    return "auto"


__all__ = ["build", "detect_provider", "Provider"]
