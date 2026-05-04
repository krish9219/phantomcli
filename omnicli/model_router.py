"""
Multi-tier model routing — Phantom's cost-optimization advantage over
single-model CLIs.

Claude Code always uses the user's chosen model for every turn, including
the easy ones (assembling a tool call, summarizing output, rewriting a
prompt). Phantom's router classifies each turn's intent and routes to a
tiered model:

  * CHEAP tier     — Haiku, Llama-8B, gpt-4o-mini. Used for: tool-arg
                     assembly, routing decisions, small summaries.
  * MID tier       — Sonnet, Llama-70B, gpt-4o. Used for: normal work
                     turns (default).
  * EXPENSIVE tier — Opus, GPT-4.1 with extended thinking. Used for:
                     reasoning turns (architectural planning, deep
                     debugging, first-turn interpretation of a complex
                     user request).

Classification signals:
  * is_reasoning_turn: prompt contains "explain", "plan", "why", "debug",
                       "architecture", "tradeoff", or has >500 chars
  * is_tool_assembly_only: the caller flagged it as a forced-tool turn
  * default: MID

The router is CONFIGURABLE per-user via config keys and honors provider
availability: if the configured cheap model isn't reachable, fall back
to mid.

API:
  * route(kind, prompt="", forced_tool=False) → ModelChoice
  * classify(prompt) → TurnKind
  * set_tiers(cheap, mid, expensive)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal, Optional

log = logging.getLogger("omnicli.model_router")

TurnKind = Literal["tool_assembly", "reasoning", "default"]
Tier     = Literal["cheap", "mid", "expensive"]


@dataclass
class ModelChoice:
    model:    str
    base_url: str = ""
    tier:     Tier = "mid"
    reason:   str  = ""


# Default tier map — users override via config.
_DEFAULTS: dict[Tier, tuple[str, str]] = {
    "cheap":     ("claude-haiku-4-5",     "https://api.anthropic.com/v1"),
    "mid":       ("claude-sonnet-4-6",    "https://api.anthropic.com/v1"),
    "expensive": ("claude-opus-4-7",      "https://api.anthropic.com/v1"),
}

# Words that push classification to reasoning
_REASONING_CUES = (
    "plan", "design", "architect", "tradeoff", "trade-off",
    "why does", "why is", "explain how", "explain why",
    "debug", "diagnose", "investigate",
    "compare", "evaluate", "best approach",
    "refactor", "optimize", "redesign",
)

_CHAR_THRESHOLD_REASONING = 500


def classify(prompt: str, *, forced_tool: bool = False) -> TurnKind:
    if forced_tool:
        return "tool_assembly"
    if not prompt:
        return "default"
    p = prompt.lower()
    for cue in _REASONING_CUES:
        if cue in p:
            return "reasoning"
    if len(prompt) > _CHAR_THRESHOLD_REASONING:
        return "reasoning"
    return "default"


def _tier_for_kind(kind: TurnKind) -> Tier:
    if kind == "tool_assembly":
        return "cheap"
    if kind == "reasoning":
        return "expensive"
    return "mid"


def _resolve_tier(tier: Tier) -> tuple[str, str]:
    """Return (model, base_url) for the tier. Reads from config first,
    falls back to hard-coded defaults."""
    try:
        from omnicli.memory import get_config
        cfg_key_model = f"tier_{tier}_model"
        cfg_key_url   = f"tier_{tier}_url"
        model = (get_config(cfg_key_model, "") or "").strip()
        url   = (get_config(cfg_key_url, "") or "").strip()
        if model:
            return model, url or _DEFAULTS[tier][1]
    except Exception:
        pass
    return _DEFAULTS[tier]


def set_tiers(cheap: Optional[tuple[str, str]] = None,
              mid:   Optional[tuple[str, str]] = None,
              expensive: Optional[tuple[str, str]] = None) -> None:
    """Test / runtime hook to override the built-in defaults."""
    if cheap:     _DEFAULTS["cheap"] = cheap
    if mid:       _DEFAULTS["mid"] = mid
    if expensive: _DEFAULTS["expensive"] = expensive


def route(
    prompt: str = "",
    forced_tool: bool = False,
    enabled:  bool = True,
) -> ModelChoice:
    """Pick a model tier and return the concrete model/url.

    If `enabled=False` (user opted out), always returns the mid-tier model
    — matches Claude Code's single-model behaviour."""
    if not enabled:
        model, url = _resolve_tier("mid")
        return ModelChoice(model=model, base_url=url, tier="mid",
                           reason="router disabled")
    kind = classify(prompt, forced_tool=forced_tool)
    tier = _tier_for_kind(kind)
    model, url = _resolve_tier(tier)
    reason = {
        "tool_assembly": "tool-only turn → cheap tier",
        "reasoning":     "reasoning cues / long prompt → expensive tier",
        "default":       "standard turn → mid tier",
    }[kind]
    return ModelChoice(model=model, base_url=url, tier=tier, reason=reason)


def router_enabled() -> bool:
    """Read the config toggle — defaults to FALSE so no behavior changes
    for existing installs until the user opts in."""
    try:
        from omnicli.memory import get_config
        return (get_config("model_router_enabled", "false") or "false").lower() == "true"
    except Exception:
        return False


__all__ = [
    "classify", "route", "set_tiers", "router_enabled",
    "ModelChoice", "TurnKind",
]
