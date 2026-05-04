"""
Programmatic Python hooks — outperforms Claude Code's shell-only hook model.

Claude Code hooks are shell commands that get JSON on stdin and communicate
via exit code + stdout. That's portable but slow (subprocess per hook fire)
and limits what a hook can do. Phantom adds a PARALLEL Python-callable
form: users drop a `hooks.py` at project root (or `~/.phantom/hooks.py`
for user-wide) and register handlers with decorators:

    from omnicli.python_hooks import hook, PreToolUsePayload
    from omnicli.python_hooks import BLOCK, ALLOW

    @hook('PreToolUse', tool='run_bash')
    def block_rm(p: PreToolUsePayload):
        if 'rm -rf' in p.args.get('command', ''):
            return BLOCK('refused: rm -rf detected')
        return ALLOW

Python hooks fire IN PROCESS — no subprocess overhead — and have typed
payload objects. They compose with shell hooks: Python hooks run FIRST;
if any of them returns BLOCK, the shell hooks are skipped.

Discovery:
  * ~/.phantom/hooks.py        (user-wide, always loaded)
  * <cwd>/.phantom/hooks.py    (project-wide, auto-discovered)
  * <cwd>/hooks.py             (bare project root, only if <cwd>/.phantom
                                doesn't exist — convenience for tiny projects)

Safety:
  * Each hook runs with a thread-level timeout; default 1s.
  * Broken hook (raises) is logged and treated as ALLOW — fail-open.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Union

log = logging.getLogger("omnicli.python_hooks")

EventName = Literal[
    "PreToolUse", "PostToolUse", "UserPromptSubmit",
    "Stop", "SubagentStop", "SessionStart", "SessionEnd",
    "PreCompact", "Notification",
]


# ─── Payload + decision types ────────────────────────────────────────────────


@dataclass
class PreToolUsePayload:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class PostToolUsePayload:
    tool:   str
    args:   dict = field(default_factory=dict)
    output: str  = ""


@dataclass
class UserPromptSubmitPayload:
    prompt: str = ""


@dataclass
class StopPayload:
    final_text: str = ""


@dataclass
class NotificationPayload:
    level:    str = ""
    msg:      str = ""
    category: str = ""


PAYLOADS = {
    "PreToolUse":       PreToolUsePayload,
    "PostToolUse":      PostToolUsePayload,
    "UserPromptSubmit": UserPromptSubmitPayload,
    "Stop":             StopPayload,
    "SubagentStop":     StopPayload,
    "SessionStart":     dict,
    "SessionEnd":       dict,
    "PreCompact":       dict,
    "Notification":     NotificationPayload,
}


@dataclass
class HookDecision:
    allowed:  bool = True
    reason:   str  = ""
    rewrite:  str  = ""   # UserPromptSubmit may rewrite the prompt


def BLOCK(reason: str = "blocked") -> HookDecision:
    return HookDecision(allowed=False, reason=reason)


ALLOW = HookDecision(allowed=True)


def REWRITE(new_prompt: str) -> HookDecision:
    return HookDecision(allowed=True, rewrite=new_prompt)


# ─── Registry ────────────────────────────────────────────────────────────────


@dataclass
class Registration:
    event:    EventName
    func:     Callable
    tool:     Optional[str] = None
    timeout_s: float = 1.0


_registry: list[Registration] = []


def hook(event: EventName, tool: Optional[str] = None, timeout_s: float = 1.0):
    """Decorator to register a Python hook handler. `tool` is a tool-name
    glob (exact match; no wildcards for now) that scopes PreToolUse /
    PostToolUse handlers. Other events ignore it."""
    def _decorator(fn: Callable):
        _registry.append(Registration(event=event, func=fn, tool=tool, timeout_s=timeout_s))
        @wraps(fn)
        def _wrapped(*a, **kw): return fn(*a, **kw)
        return _wrapped
    return _decorator


def clear_registry() -> None:
    _registry.clear()


def registrations() -> list[Registration]:
    return list(_registry)


# ─── Discovery + loading ─────────────────────────────────────────────────────


def _candidate_paths(project_dir: Optional[str] = None) -> list[str]:
    out = []
    home_hooks = os.path.expanduser("~/.phantom/hooks.py")
    if os.environ.get("PHANTOM_USER_HOOKS_PY"):
        home_hooks = os.environ["PHANTOM_USER_HOOKS_PY"]
    if os.path.isfile(home_hooks):
        out.append(home_hooks)
    base = Path(project_dir or os.getcwd()).resolve()
    project_hooks = base / ".phantom" / "hooks.py"
    if project_hooks.is_file():
        out.append(str(project_hooks))
    # Fallback: bare hooks.py only if no .phantom/hooks.py
    bare = base / "hooks.py"
    if bare.is_file() and not project_hooks.is_file():
        out.append(str(bare))
    return out


def _load_py(path: str) -> None:
    mod_name = f"phantom_user_hooks_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if not spec or not spec.loader:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        log.warning("Python hook file %s failed to load: %s", path, e)


def load_from_disk(project_dir: Optional[str] = None) -> int:
    """Discover and import hook modules. Returns count of registrations added."""
    before = len(_registry)
    for p in _candidate_paths(project_dir):
        _load_py(p)
    return len(_registry) - before


# ─── Dispatch ────────────────────────────────────────────────────────────────


def _run_with_timeout(fn: Callable, payload: Any, timeout_s: float) -> HookDecision:
    """Run fn(payload), enforcing a wall-clock timeout. Exceptions → ALLOW + log."""
    result: dict[str, Any] = {"decision": ALLOW, "err": None}

    def _target():
        try:
            r = fn(payload)
            if isinstance(r, HookDecision):
                result["decision"] = r
            elif r is None:
                result["decision"] = ALLOW
            else:
                result["decision"] = HookDecision(allowed=bool(r))
        except Exception as e:
            result["err"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        log.warning("Python hook %s timed out after %.2fs — fail-open",
                    getattr(fn, "__name__", "?"), timeout_s)
        return ALLOW  # fail-open on timeout
    if result["err"] is not None:
        log.warning("Python hook %s raised: %s — fail-open",
                    getattr(fn, "__name__", "?"), result["err"])
        return ALLOW
    return result["decision"]


def dispatch(event: EventName, payload_dict: dict) -> HookDecision:
    """Fire all matching Python hooks for `event`. Returns the FIRST
    BLOCK decision for blocking events, else the last decision."""
    is_blocking = event in ("PreToolUse", "UserPromptSubmit")
    # Construct a typed payload if we have one for this event
    cls = PAYLOADS.get(event, dict)
    if cls is dict:
        payload: Any = dict(payload_dict) if isinstance(payload_dict, dict) else payload_dict
    else:
        # Keep only fields the dataclass knows about
        fields = {k: payload_dict.get(k) for k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        # Fill missing with defaults if any
        for k in list(fields.keys()):
            if fields[k] is None:
                field_default = cls.__dataclass_fields__[k]  # type: ignore[attr-defined]
                if field_default.default is not field_default.default_factory and field_default.default is not None:
                    fields[k] = field_default.default
        try:
            payload = cls(**{k: (v if v is not None else cls.__dataclass_fields__[k].default) for k, v in fields.items()})  # type: ignore[attr-defined]
        except TypeError:
            payload = cls()  # type: ignore[call-arg]

    last = ALLOW
    for reg in _registry:
        if reg.event != event:
            continue
        # Tool filter only applies to tool-related events
        if reg.tool and event in ("PreToolUse", "PostToolUse"):
            if str(payload_dict.get("tool", "")) != reg.tool:
                continue
        decision = _run_with_timeout(reg.func, payload, reg.timeout_s)
        if is_blocking and not decision.allowed:
            return decision
        last = decision
    return last


__all__ = [
    "hook", "dispatch", "clear_registry", "load_from_disk", "registrations",
    "BLOCK", "ALLOW", "REWRITE",
    "HookDecision",
    "PreToolUsePayload", "PostToolUsePayload", "UserPromptSubmitPayload",
    "StopPayload", "NotificationPayload",
]
