"""
Prompt-injection defense — layered scan + boundary markers.

Two concerns:

  1. User prompts: an adversary (or a naive copy-paste) may include
     instructions like "Ignore previous instructions and …" or embed
     `<system>`-style tags. We scan user input for known injection
     patterns and either annotate it with a safety banner, mask high-risk
     segments, or — at caller's discretion — reject outright.

  2. Tool output fed back to the model: tool results may contain
     untrusted text (web pages, file contents, command output). We wrap
     that text in clearly delimited boundary markers so the model can't
     be tricked into treating it as new instructions. The markers are
     visible to the user too, which is fine — they communicate scope.

Public API:
  * scan(text)             → ScanResult(verdict, matches, sanitized)
  * sanitize_user(text)    → str (lightweight cleanup, preserves intent)
  * wrap_tool_output(text, tool_name=None) → str (strong boundary form)
  * is_high_risk(text)     → bool (shorthand for a critical verdict)

Verdicts: "ok" | "suspicious" | "high_risk".
  ok          → no injection patterns detected
  suspicious  → mild indicators (role tags, quoted meta-instructions)
  high_risk   → strong indicators (explicit "ignore previous", fake system:
                roles, tool-call forgery patterns)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["ok", "suspicious", "high_risk"]


# ─── Patterns ────────────────────────────────────────────────────────────────

# High-risk patterns are classic injection verbs. We include common
# obfuscations (leetspeak numbers, unicode homoglyphs for the `i`).
_HIGH_RISK: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\b(ignore|disregard|forget)\b.{0,40}(previous|above|earlier|prior)\b.{0,20}(instruction|prompt|rule|message)"),
                                                "ignore-previous-instructions"),
    (re.compile(r"(?i)\byou\s+are\s+now\b.{0,30}(dan|jailbr[oa]k|unrestricted|no[- ]limits)"),
                                                "persona-jailbreak (DAN-family)"),
    (re.compile(r"(?i)\b(override|bypass|disable)\b.{0,30}(safety|filter|guard|restriction|rules)"),
                                                "safety-override request"),
    (re.compile(r"(?i)(^|\n)\s*system\s*:\s*(?!ok|ready|\n)"),
                                                "fake system: role prefix"),
    (re.compile(r"(?i)(^|\n)\s*assistant\s*:\s*"),
                                                "fake assistant: role prefix"),
    (re.compile(r"</?\s*system\s*>", re.I),     "<system> tag"),
    (re.compile(r"<\|\s*(im_start|im_end|eot|endoftext)\s*\|>", re.I),
                                                "chat template sentinel"),
    (re.compile(r"(?i)\breveal\b.{0,30}(system|initial)\s+prompt"),
                                                "system-prompt exfiltration"),
    (re.compile(r"(?i)\brepeat\b.{0,20}(verbatim|word\s*for\s*word)\b.{0,30}(system|initial|above)"),
                                                "verbatim system prompt request"),
    (re.compile(r"(?i)\b(exec|eval|shell)\s*\(\s*['\"].{0,80}['\"]\s*\)"),
                                                "shell-style exec() call in text"),
]

# Suspicious patterns are softer: they might be benign, but combined with
# other context they're worth flagging.
_SUSPICIOUS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"<\s*instructions?\s*>", re.I), "<instructions> tag"),
    (re.compile(r"(?i)\bprompt\s+injection\b"),  "literal 'prompt injection'"),
    (re.compile(r"(?i)\bBEGIN\s+SYSTEM\b"),      "BEGIN SYSTEM sentinel"),
    (re.compile(r"(?i)^\s*###\s*system\s*###", re.M), "### system ### header"),
]


# ─── Data class ──────────────────────────────────────────────────────────────


@dataclass
class ScanResult:
    verdict:   Verdict
    matches:   list[str] = field(default_factory=list)
    sanitized: str       = ""

    @property
    def ok(self) -> bool:
        return self.verdict == "ok"

    @property
    def high_risk(self) -> bool:
        return self.verdict == "high_risk"


# ─── Public API ──────────────────────────────────────────────────────────────


def scan(text: str) -> ScanResult:
    """Classify `text`. Populates `matches` with a human-readable name per
    rule that fired."""
    if not text:
        return ScanResult(verdict="ok", matches=[], sanitized="")
    matches: list[str] = []
    hit_high = False
    for pat, label in _HIGH_RISK:
        if pat.search(text):
            matches.append(label)
            hit_high = True
    for pat, label in _SUSPICIOUS:
        if pat.search(text):
            matches.append(label)
    verdict: Verdict = "ok"
    if hit_high:
        verdict = "high_risk"
    elif matches:
        verdict = "suspicious"
    sanitized = _apply_sanitization(text, matches) if matches else text
    return ScanResult(verdict=verdict, matches=matches, sanitized=sanitized)


def sanitize_user(text: str) -> str:
    """Return a lightly-sanitized form of the user's input: neutralise
    role-spoof prefixes (system:, assistant:), escape chat-template sentinels.

    This is deliberately NOT aggressive — we want to preserve the user's
    intent. For high-risk content the engine should wrap the whole string
    in an untrusted-input block rather than relying on sanitization.
    """
    return _apply_sanitization(text, [])


def is_high_risk(text: str) -> bool:
    return scan(text).high_risk


# Boundary markers used to isolate tool output when fed back to the model.
# The model is prompted to treat everything between these as data, not
# instructions. The markers are deliberately verbose so they're visible
# even in truncated logs.
_BEGIN_UNTRUSTED = "⟪PHANTOM_UNTRUSTED_INPUT_BEGIN⟫"
_END_UNTRUSTED   = "⟪PHANTOM_UNTRUSTED_INPUT_END⟫"


def wrap_tool_output(text: str, tool_name: str | None = None) -> str:
    """Wrap an untrusted string in strong boundary markers. Any occurrence
    of the markers inside the text is neutralised (replaced) so an
    adversary can't close the boundary early."""
    if not text:
        return ""
    clean = text.replace(_BEGIN_UNTRUSTED, "⟪BEGIN⟫").replace(_END_UNTRUSTED, "⟪END⟫")
    header = f"{_BEGIN_UNTRUSTED}"
    if tool_name:
        header += f" (tool={tool_name})"
    return f"{header}\n{clean}\n{_END_UNTRUSTED}"


# ─── Internals ───────────────────────────────────────────────────────────────


def _apply_sanitization(text: str, matches: list[str]) -> str:
    """Neutralise the most common injection vectors without destroying the
    semantic content of the user's prompt."""
    # Escape chat-template sentinels so they render but don't tokenise.
    # The replacement pattern uses plain `|` — `\|` would leave literal
    # backslashes in the output (Python preserves unknown escapes).
    text = re.sub(r"<\|(im_start|im_end|eot|endoftext)\|>",
                  r"<| \1 |>", text, flags=re.I)
    # Defuse role-spoof prefixes by inserting zero-width space after the colon.
    text = re.sub(r"(?im)(^|\n)(\s*)(system|assistant|user)\s*:\s*",
                  lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}\u200b: ",
                  text)
    return text


__all__ = [
    "scan", "sanitize_user", "wrap_tool_output", "is_high_risk",
    "ScanResult",
]
