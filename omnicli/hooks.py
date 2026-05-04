"""
Shell-command lifecycle hooks — inspired by Claude Code's hook system.

A hook is a shell command the user registers to run at a specific lifecycle
event. If the hook exits non-zero, the event is **blocked** (the tool call
is cancelled, the prompt is rejected, etc.). Hooks receive JSON on stdin
describing the event and can read stdout/stderr of the tool from the
environment.

Events:
  PreToolUse     — fires before a tool executes. stdin: {tool, args}.
                   Non-zero exit blocks the tool call.
  PostToolUse    — fires after a tool executes. stdin: {tool, args, output}.
                   Exit code ignored (informational only).
  UserPromptSubmit — fires when the user submits a prompt. stdin: {prompt}.
                     Non-zero exit blocks the prompt.
  Stop           — fires when the agent finishes a turn. stdin: {final_text}.
                   Exit code ignored.

Config (~/.phantom/hooks.json):
  {
    "PreToolUse":    [{"match": "run_bash", "cmd": "~/phantom-guard.sh"}],
    "PostToolUse":   [{"match": "*",         "cmd": "logger -t phantom"}],
    "UserPromptSubmit": [],
    "Stop": []
  }

  - `match` is a fnmatch pattern against the tool name (or always-match "*"
    for lifecycle events that don't have one)
  - `cmd` is a shell command string executed via /bin/sh (or cmd.exe on Win)
  - A 5-second default timeout protects against hung hooks
  - Missing file or malformed JSON = no hooks configured (not an error)
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Literal

log = logging.getLogger("omnicli.hooks")

EventName = Literal[
    "PreToolUse",       # before a tool executes — non-zero exit BLOCKS
    "PostToolUse",      # after a tool executes — exit ignored (informational)
    "UserPromptSubmit", # when the user submits a prompt — non-zero BLOCKS, stdout can REWRITE
    "Stop",             # when the top-level agent finishes a turn
    "SubagentStop",     # when a subagent finishes — exit ignored
    "SessionStart",     # at REPL startup / first prompt in a session
    "SessionEnd",       # at REPL exit / Ctrl-D
    "PreCompact",       # before context compaction — exit ignored, stdout logs
    "Notification",     # informational event (errors, warnings, cost alerts)
]

_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.phantom/hooks.json")
_DEFAULT_TIMEOUT_S = 5


@dataclass
class HookResult:
    allowed: bool
    reason: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    hook_cmd: str = ""


def _config_path() -> str:
    return os.environ.get("PHANTOM_HOOKS_CONFIG", _DEFAULT_CONFIG_PATH)


def _load_config() -> dict[str, list[dict[str, str]]]:
    path = _config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # Normalize: each event maps to a list of {match, cmd, timeout?}
        out: dict[str, list[dict[str, str]]] = {}
        for k, v in data.items():
            if not isinstance(v, list):
                continue
            clean: list[dict[str, str]] = []
            for item in v:
                if not isinstance(item, dict):
                    continue
                cmd = item.get("cmd", "")
                if not cmd:
                    continue
                clean.append({
                    "match":   str(item.get("match", "*")),
                    "cmd":     cmd,
                    "timeout": str(item.get("timeout", _DEFAULT_TIMEOUT_S)),
                })
            if clean:
                out[k] = clean
        return out
    except (OSError, json.JSONDecodeError) as e:
        log.warning("hooks config unreadable: %s", e)
        return {}


def _run_one(hook: dict[str, str], payload: dict[str, Any]) -> HookResult:
    cmd = hook["cmd"]
    try:
        t = max(1, min(int(hook.get("timeout", _DEFAULT_TIMEOUT_S)), 60))
    except (TypeError, ValueError):
        t = _DEFAULT_TIMEOUT_S
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=t,
        )
        return HookResult(
            allowed=(proc.returncode == 0),
            reason=f"hook exit {proc.returncode}" if proc.returncode != 0 else "",
            stdout=proc.stdout[:2000],
            stderr=proc.stderr[:2000],
            exit_code=proc.returncode,
            hook_cmd=cmd,
        )
    except subprocess.TimeoutExpired:
        return HookResult(
            allowed=False,
            reason=f"hook timed out after {t}s",
            exit_code=-1,
            hook_cmd=cmd,
        )
    except Exception as e:
        # A broken hook config shouldn't brick the whole CLI — allow and warn.
        log.warning("hook exec failed: %s", e)
        return HookResult(
            allowed=True,
            reason=f"hook exec error (fail-open): {e}",
            exit_code=-2,
            hook_cmd=cmd,
        )


# Events whose hook exit code is binding. Every other event is informational
# (exit is logged but cannot block the pipeline).
_BLOCKING_EVENTS: frozenset[str] = frozenset({
    "PreToolUse", "UserPromptSubmit",
})


def _matches(pattern: str, target: str) -> bool:
    if not pattern or pattern == "*":
        return True
    return fnmatch.fnmatchcase(target, pattern)


def _match_target(event: EventName, payload: dict[str, Any]) -> str:
    """Pick the payload field the `match` pattern filters against, per event.
    Tool events match on tool name; command events match on the command
    name; everything else uses "*" (always matches)."""
    if event in ("PreToolUse", "PostToolUse"):
        return str(payload.get("tool", "")) if isinstance(payload, dict) else ""
    if event == "UserPromptSubmit":
        return str(payload.get("prompt", "")) if isinstance(payload, dict) else ""
    if event == "Notification":
        return str(payload.get("level", "")) if isinstance(payload, dict) else ""
    return "*"


def dispatch(event: EventName, payload: dict[str, Any]) -> HookResult:
    """Fire all hooks registered for `event`. Returns the FIRST blocking
    result (allowed=False) for blocking events, else the last success.

    Binding exit codes (exit non-zero BLOCKS): PreToolUse, UserPromptSubmit.
    Every other event treats exit purely as a log signal — allowed is always
    True in the returned HookResult regardless of what the hook emitted.

    For UserPromptSubmit the hook's **stdout** can REWRITE the prompt: if the
    hook exits zero and prints non-empty stdout, the caller should use that
    as the new prompt. See `_apply_prompt_rewrite`.
    """
    cfg = _load_config()
    hooks = cfg.get(event, [])
    target = _match_target(event, payload)
    is_blocking = event in _BLOCKING_EVENTS
    agg = HookResult(allowed=True, reason="no matching hooks")
    for h in hooks:
        if not _matches(h.get("match", "*"), target):
            continue
        r = _run_one(h, payload)
        if is_blocking and not r.allowed:
            return r
        # Non-blocking event: swallow the blocker so caller sees allowed=True,
        # but preserve the captured stdout/stderr + exit code for logging.
        if not is_blocking:
            r.allowed = True
            r.reason = r.reason or "non-blocking event"
        agg = r
    return agg


def apply_prompt_rewrite(payload: dict[str, Any]) -> tuple[bool, str]:
    """Fire UserPromptSubmit hooks and return (allow, effective_prompt).

    Semantics matching Claude Code:
      - Any hook exiting non-zero → (False, reason) → block.
      - Zero-exit hook printing non-empty stdout → rewrite the prompt
        (stdout is trimmed; if hooks chain, each one sees the previous
        rewrite).
      - Zero-exit hook with empty stdout → pass through unchanged.
    """
    cfg = _load_config()
    hooks = cfg.get("UserPromptSubmit", [])
    prompt = str(payload.get("prompt", ""))
    for h in hooks:
        target = prompt  # each iteration matches against current prompt
        if not _matches(h.get("match", "*"), target):
            continue
        r = _run_one(h, {**payload, "prompt": prompt})
        if not r.allowed:
            return False, r.stderr or r.reason or "UserPromptSubmit blocked"
        rewrite = (r.stdout or "").strip()
        if rewrite:
            prompt = rewrite
    return True, prompt


def is_configured() -> bool:
    """True if the user has any hooks file present (even empty)."""
    return os.path.isfile(_config_path())


__all__ = ["dispatch", "HookResult", "is_configured", "apply_prompt_rewrite"]
