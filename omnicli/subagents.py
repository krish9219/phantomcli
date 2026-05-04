"""
Typed subagent registry — mirrors Claude Code's Agent tool subagent_type.

Where AgentOrchestrator is Phantom's multi-file parallel builder, this
module adds the OTHER Claude-Code-style agent flavor: short-lived,
narrowly-scoped helpers the main agent can delegate to ("research this",
"plan that", "review these changes"). Each type has:

  * name          — stable identifier used in tool calls
  * description   — one-line summary shown to the router
  * system_prompt — the agent's persona / rules
  * allowed_tools — whitelist of tool names the subagent can call; an
                    empty list means "all tools" (rarely what you want).
  * default_model — provider-specific model override (optional)
  * timeout_s     — hard wall-clock cap

Registry:
  * Built-ins: general-purpose, explore, plan, code-reviewer, security-reviewer
  * User types: loaded from ~/.phantom/agents/<name>.md (frontmatter + body)

API:
  register(SubagentType) | get(name) | list() | load_user_agents()
  Dispatch is performed by the engine; this module is state-only.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("omnicli.subagents")


# ─── Type definition ─────────────────────────────────────────────────────────


@dataclass
class SubagentType:
    name:          str
    description:   str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    default_model: str  = ""
    timeout_s:     int  = 300
    is_builtin:    bool = True


_BUILTINS: list[SubagentType] = [
    SubagentType(
        name="general-purpose",
        description=(
            "Default subagent. Can use any tool. Good for open-ended research, "
            "multi-step investigation, or tasks that don't fit a specialised type."
        ),
        system_prompt=(
            "You are a general-purpose subagent. You have access to all tools. "
            "Stay focused on the task the main agent delegated. Return a single "
            "concise text answer when you're done — the main agent will use it "
            "as your tool output. Do not ask clarifying questions; make your "
            "best judgment and state your assumption."
        ),
        allowed_tools=[],  # empty → all tools permitted
        timeout_s=600,
    ),
    SubagentType(
        name="explore",
        description=(
            "Fast read-only codebase exploration. Use for 'find the file that…' "
            "or 'summarise how X works'. Faster and cheaper than general-purpose."
        ),
        system_prompt=(
            "You are an Explore subagent. Use Glob, Grep, Read, and web_search "
            "ONLY. You must NOT write, edit, or execute. Your job: find the "
            "specific files/lines/concepts the main agent asked about and "
            "return a short, structured summary with file:line citations. "
            "Be exhaustive within scope but do not ramble."
        ),
        allowed_tools=["glob", "grep", "read_file", "web_search"],
        timeout_s=180,
    ),
    SubagentType(
        name="plan",
        description=(
            "Software-architect planner. Reads the relevant code, designs an "
            "implementation plan, returns a step-by-step plan with file paths "
            "and architectural trade-offs. Never writes code."
        ),
        system_prompt=(
            "You are a Plan subagent. Read only. Produce a step-by-step "
            "implementation plan with: (1) files to create/edit, (2) the "
            "ordering and why, (3) key trade-offs or risks, (4) test strategy. "
            "Output concise markdown. Never emit write_file or edit_file."
        ),
        allowed_tools=["glob", "grep", "read_file"],
        timeout_s=240,
    ),
    SubagentType(
        name="code-reviewer",
        description=(
            "Review a pull request or a diff. Flags correctness, maintainability, "
            "test coverage, and style issues. Read-only."
        ),
        system_prompt=(
            "You are a Code Reviewer subagent. Read only. Produce a review with: "
            "(a) correctness risks, (b) maintainability concerns, (c) missing "
            "tests, (d) style nits (kept brief). Quote file:line for every "
            "comment. No rewrites — leave that to the main agent."
        ),
        allowed_tools=["glob", "grep", "read_file", "run_bash"],
        timeout_s=240,
    ),
    SubagentType(
        name="security-reviewer",
        description=(
            "Review code for security issues: command injection, path traversal, "
            "XSS, SSRF, secret leakage, missing permission checks."
        ),
        system_prompt=(
            "You are a Security Reviewer subagent. Read only. Focus on OWASP-class "
            "issues, authn/z gaps, and common supply-chain traps. Output a "
            "severity-ordered list (critical → informational) with file:line "
            "citations and a concrete fix suggestion per finding."
        ),
        allowed_tools=["glob", "grep", "read_file", "run_bash"],
        timeout_s=300,
    ),
    SubagentType(
        name="statusline-setup",
        description=(
            "Configure or modify the Phantom REPL status line (memory, trust, "
            "model display). Writes to settings only."
        ),
        system_prompt=(
            "You are the Status-Line Setup subagent. You modify the statusline "
            "config only — not code. Read the current setting, propose the "
            "change, write the update, confirm."
        ),
        allowed_tools=["read_file", "write_file", "edit_file"],
        timeout_s=120,
    ),
]


class Registry:
    def __init__(self):
        self._by_name: dict[str, SubagentType] = {}

    def register(self, t: SubagentType) -> None:
        self._by_name[t.name.lower()] = t

    def unregister(self, name: str) -> bool:
        return self._by_name.pop(name.lower(), None) is not None

    def get(self, name: str) -> Optional[SubagentType]:
        return self._by_name.get(name.lower())

    def list(self) -> list[SubagentType]:
        return sorted(self._by_name.values(), key=lambda t: t.name)

    def tool_allowed(self, agent_name: str, tool_name: str) -> bool:
        t = self.get(agent_name)
        if t is None:
            return False  # unknown agent — deny
        if not t.allowed_tools:
            return True   # empty allowlist → all tools permitted
        return tool_name.lower() in {x.lower() for x in t.allowed_tools}


# ─── User-defined agents from ~/.phantom/agents/*.md ─────────────────────────


_USER_AGENTS_DIR = os.path.expanduser("~/.phantom/agents")


def _user_agents_dir() -> str:
    return os.environ.get("PHANTOM_AGENTS_DIR", _USER_AGENTS_DIR)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV_LINE_RE     = re.compile(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$")


def _parse_agent_md(body: str) -> tuple[dict, str]:
    """Return (metadata, system_prompt_body)."""
    m = _FRONTMATTER_RE.match(body)
    if not m:
        return {}, body.lstrip()
    header = m.group(1)
    rest   = body[m.end():].lstrip()
    meta: dict = {}
    for line in header.splitlines():
        km = _KV_LINE_RE.match(line.strip())
        if not km:
            continue
        k, v = km.group(1).lower(), km.group(2).strip()
        # Strip matching quotes
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        meta[k] = v
    return meta, rest


def _parse_tool_list(raw: str) -> list[str]:
    if not raw:
        return []
    # Comma or whitespace separated; drop empties
    return [t.strip() for t in re.split(r"[,\s]+", raw) if t.strip()]


def load_user_agents(reg: Registry) -> int:
    d = _user_agents_dir()
    if not os.path.isdir(d):
        return 0
    loaded = 0
    for fn in os.listdir(d):
        if not fn.endswith(".md"):
            continue
        name = fn[:-3]
        path = os.path.join(d, fn)
        try:
            body = open(path, "r", encoding="utf-8").read()
        except OSError:
            continue
        meta, prompt = _parse_agent_md(body)
        try:
            timeout = int(meta.get("timeout_s", "300"))
        except ValueError:
            timeout = 300
        reg.register(SubagentType(
            name=meta.get("name", name),
            description=meta.get("description", f"User subagent: {name}"),
            system_prompt=prompt,
            allowed_tools=_parse_tool_list(meta.get("tools", "")),
            default_model=meta.get("model", ""),
            timeout_s=timeout,
            is_builtin=False,
        ))
        loaded += 1
    return loaded


def _build_default_registry() -> Registry:
    r = Registry()
    for t in _BUILTINS:
        r.register(t)
    load_user_agents(r)
    return r


DEFAULT_REGISTRY: Registry = _build_default_registry()


def reload_registry() -> Registry:
    global DEFAULT_REGISTRY
    DEFAULT_REGISTRY = _build_default_registry()
    return DEFAULT_REGISTRY


def get(name: str) -> Optional[SubagentType]:
    return DEFAULT_REGISTRY.get(name)


def list_types() -> list[SubagentType]:
    return DEFAULT_REGISTRY.list()


__all__ = [
    "SubagentType", "Registry",
    "get", "list_types",
    "DEFAULT_REGISTRY", "reload_registry", "load_user_agents",
]
