"""
Slash command registry — mirrors Claude Code's /command pattern.

The user types `/name args...` at the REPL prompt. The registry matches
the name against:
  1. Built-in commands registered at import time.
  2. User-defined commands in ~/.phantom/commands/*.md (the first line
     after YAML frontmatter is the description; the body is the prompt
     template, with {args} substituted).

Built-ins include at minimum: /help /clear /model /memory /perm /hook
/session /compact /cost /exit.

Design:
  * `parse(line)` → (name, args_str) | None if not a slash command
  * `dispatch(line, ctx)` → SlashResult — runs the command handler and
    returns the text to display (or a special signal like CLEAR / EXIT).
  * Handlers take (args: str, ctx: dict) and return str | SlashResult.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional


SLASH_RE = re.compile(r"^\s*/(\w[\w\-]*)\s*(.*)$", re.DOTALL)


@dataclass
class SlashResult:
    """Return value from a slash command handler.

    `text` is what the REPL prints to the user. Special semantics:
      - clear=True   → REPL should wipe scrollback + history for the session
      - exit=True    → REPL should terminate cleanly
      - rewrite=str  → replace the user prompt with this text and continue
    """
    text:    str   = ""
    clear:   bool  = False
    exit:    bool  = False
    rewrite: str   = ""
    error:   bool  = False


HandlerFn = Callable[[str, dict], "str | SlashResult"]


@dataclass
class SlashCommand:
    name:        str
    description: str
    handler:     HandlerFn
    usage:       str = ""
    is_builtin:  bool = True


class Registry:
    def __init__(self):
        self._commands: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        self._commands[cmd.name.lower()] = cmd

    def unregister(self, name: str) -> bool:
        return self._commands.pop(name.lower(), None) is not None

    def list(self) -> list[SlashCommand]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def get(self, name: str) -> Optional[SlashCommand]:
        return self._commands.get(name.lower())

    def dispatch(self, line: str, ctx: Optional[dict] = None) -> SlashResult:
        parsed = parse(line)
        if parsed is None:
            return SlashResult(text=line, error=False)
        name, args = parsed
        cmd = self.get(name)
        if cmd is None:
            return SlashResult(
                text=f"Unknown command: /{name}. Try /help for a list.",
                error=True,
            )
        try:
            out = cmd.handler(args, ctx or {})
        except Exception as e:
            return SlashResult(text=f"/{name} failed: {e}", error=True)
        if isinstance(out, SlashResult):
            return out
        return SlashResult(text=str(out))


def parse(line: str) -> Optional[tuple[str, str]]:
    """Parse `/name args...` → (name, args). Returns None if not a slash
    command."""
    m = SLASH_RE.match(line or "")
    if not m:
        return None
    return m.group(1), m.group(2).strip()


# ─── Built-in command handlers ────────────────────────────────────────────────


def _h_help(args: str, ctx: dict) -> SlashResult:
    reg: Registry = ctx.get("registry", DEFAULT_REGISTRY)
    cmds = reg.list()
    if args.strip():
        target = reg.get(args.strip().lstrip("/"))
        if not target:
            return SlashResult(text=f"No such command: /{args.strip()}", error=True)
        out = [f"/{target.name} — {target.description}"]
        if target.usage:
            out.append(f"  usage: {target.usage}")
        return SlashResult(text="\n".join(out))
    lines = ["Available slash commands:"]
    for c in cmds:
        tag = "" if c.is_builtin else "  (user)"
        lines.append(f"  /{c.name:<12} {c.description}{tag}")
    return SlashResult(text="\n".join(lines))


def _h_clear(args: str, ctx: dict) -> SlashResult:
    return SlashResult(text="Session cleared.", clear=True)


def _h_exit(args: str, ctx: dict) -> SlashResult:
    return SlashResult(text="Bye.", exit=True)


def _h_model(args: str, ctx: dict) -> SlashResult:
    from omnicli.memory import get_config, save_config
    if not args.strip():
        current = get_config("main_model", "(unset)")
        return SlashResult(text=f"Current model: {current}")
    save_config("main_model", args.strip())
    return SlashResult(text=f"Model set to: {args.strip()}")


def _h_memory(args: str, ctx: dict) -> SlashResult:
    from omnicli.memory import get_config
    lines = ["Memory overview:"]
    keys = ("main_model", "main_url", "trust_level", "work_dir", "bot_name")
    for k in keys:
        v = get_config(k, "")
        lines.append(f"  {k}: {v or '(unset)'}")
    return SlashResult(text="\n".join(lines))


def _h_perm(args: str, ctx: dict) -> SlashResult:
    """Usage: /perm list | /perm allow <pattern> | /perm deny <pattern> | /perm remove <pattern>"""
    from omnicli.memory import get_config, save_config
    toks = args.strip().split(maxsplit=1)
    if not toks or toks[0] in ("", "list", "ls"):
        allow = get_config("permissions_allow", "") or ""
        deny  = get_config("permissions_deny",  "") or ""
        ask   = get_config("permissions_ask",   "") or ""
        return SlashResult(text=(
            f"Allow:\n{allow or '  (none)'}\n\n"
            f"Deny:\n{deny or '  (none)'}\n\n"
            f"Ask:\n{ask or '  (none)'}"
        ))
    op = toks[0].lower()
    if op not in ("allow", "deny", "ask", "remove", "rm"):
        return SlashResult(text=(
            "Usage: /perm list | /perm allow <pattern> | "
            "/perm deny <pattern> | /perm remove <pattern>"
        ), error=True)
    if len(toks) < 2:
        return SlashResult(text=f"/perm {op}: pattern required", error=True)
    pat = toks[1].strip()
    key_map = {"allow": "permissions_allow", "deny": "permissions_deny", "ask": "permissions_ask"}
    if op in ("remove", "rm"):
        removed = []
        for k in key_map.values():
            cur = (get_config(k, "") or "").splitlines()
            new = [p for p in cur if p.strip() != pat]
            if len(new) != len(cur):
                save_config(k, "\n".join(new))
                removed.append(k.replace("permissions_", ""))
        return SlashResult(text=f"Removed {pat!r} from: {', '.join(removed) or '(none)'}")
    key = key_map[op]
    cur = (get_config(key, "") or "").splitlines()
    if pat not in cur:
        cur.append(pat)
    save_config(key, "\n".join(p for p in cur if p.strip()))
    return SlashResult(text=f"Added to {op}: {pat}")


def _h_hook(args: str, ctx: dict) -> SlashResult:
    from omnicli.hooks import is_configured, _config_path
    path = _config_path()
    if not is_configured():
        return SlashResult(text=(
            f"No hooks configured. Create {path} with a JSON object like:\n"
            '  {"PreToolUse": [{"match":"run_bash","cmd":"./guard.sh"}]}'
        ))
    try:
        data = open(path, "r", encoding="utf-8").read()
    except OSError as e:
        return SlashResult(text=f"Cannot read {path}: {e}", error=True)
    return SlashResult(text=f"Hooks ({path}):\n{data}")


def _h_session(args: str, ctx: dict) -> SlashResult:
    """Usage: /session list | /session save <name> | /session load <id> | /session delete <id>"""
    try:
        from omnicli.sessions import list_sessions, save_session, load_session, delete_session
    except ImportError:
        return SlashResult(text="Session module not available.", error=True)
    toks = args.strip().split(maxsplit=1)
    op = (toks[0] if toks else "list").lower()
    if op in ("list", "ls"):
        items = list_sessions()
        if not items:
            return SlashResult(text="No saved sessions.")
        lines = ["Saved sessions:"]
        for s in items:
            lines.append(f"  {s['id']}  {s.get('name', '(unnamed)')}  {s.get('updated', '')}")
        return SlashResult(text="\n".join(lines))
    if op == "save":
        name = toks[1] if len(toks) > 1 else ""
        sid = save_session(ctx, name=name)
        return SlashResult(text=f"Saved session {sid}")
    if op == "load":
        if len(toks) < 2:
            return SlashResult(text="Usage: /session load <id>", error=True)
        s = load_session(toks[1])
        if s is None:
            return SlashResult(text=f"No session {toks[1]!r}", error=True)
        return SlashResult(text=f"Loaded session {toks[1]}: {len(s.get('messages', []))} messages")
    if op == "delete":
        if len(toks) < 2:
            return SlashResult(text="Usage: /session delete <id>", error=True)
        ok = delete_session(toks[1])
        return SlashResult(text=("Deleted." if ok else "Not found."), error=not ok)
    return SlashResult(text="Usage: /session list | save <name> | load <id> | delete <id>", error=True)


def _h_compact(args: str, ctx: dict) -> SlashResult:
    from omnicli.context_compact import compact, estimate_messages
    msgs = ctx.get("messages") or []
    if not msgs:
        return SlashResult(text="No conversation to compact.")
    before = estimate_messages(msgs)
    new, stats = compact(
        msgs,
        budget=int(ctx.get("budget", 128000)),
        ratio=0.0,  # force compaction
        keep_recent=int(ctx.get("keep_recent", 8)),
    )
    ctx["messages"] = new
    return SlashResult(text=(
        f"Compacted: {stats.before_count} → {stats.after_count} messages, "
        f"{before} → {stats.after_tokens} tokens."
    ))


def _h_cost(args: str, ctx: dict) -> SlashResult:
    from omnicli.context_compact import estimate_messages
    msgs = ctx.get("messages") or []
    tok = estimate_messages(msgs)
    budget = int(ctx.get("budget", 128000))
    pct = (tok / budget * 100) if budget else 0
    return SlashResult(text=(
        f"Context: {tok:,} tokens used / {budget:,} budget ({pct:.1f}%)"
    ))


def _h_web(args: str, ctx: dict) -> SlashResult:
    """/web <query> — search the web and return a summary. Matches Claude
    Code's pattern of "search + read + summarize" as a single explicit
    command so the user doesn't accidentally end up in project-creation
    mode when all they wanted was information.
    """
    query = args.strip()
    if not query:
        return SlashResult(
            text="Usage: /web <query>\n\nExamples:\n"
                 "  /web latest IPL 2026 match results\n"
                 "  /web bitcoin price now\n"
                 "  /web top tech news today",
            error=True,
        )
    lines: list[str] = [f"🌐 Searching: {query!r}"]
    # Step 1: DDG search for relevant URLs
    urls: list[str] = []
    try:
        from omnicli.engine import _web_search
        import re as _re
        raw = _web_search(query, max_results=6)
        for u in _re.findall(r'https?://[^\s\]\)\'"<>]+', raw):
            u = u.rstrip(".,;:!?)")
            if any(bad in u.lower() for bad in (
                "google.com/url", "duckduckgo.com/y.js", "bing.com/ck",
            )):
                continue
            if u not in urls:
                urls.append(u)
            if len(urls) >= 4:
                break
    except Exception as e:
        return SlashResult(text=f"/web: search failed: {e}", error=True)

    if not urls:
        return SlashResult(text=f"/web: no results for {query!r}", error=True)

    lines.append(f"\nFound {len(urls)} relevant URLs, scraping top 3…\n")

    # Step 2: scrape via the browser waterfall
    scraped: list[tuple[str, str]] = []
    try:
        from omnicli.browser import run_browser
        for u in urls[:3]:
            lines.append(f"  → {u[:80]}")
            text = run_browser(u) or ""
            if text and "Could not fetch" not in text and len(text) > 200:
                scraped.append((u, text[:4000]))
    except Exception as e:
        return SlashResult(text=f"/web: scrape failed: {e}", error=True)

    if not scraped:
        return SlashResult(
            text="\n".join(lines + ["", "All pages blocked / unreachable. "
                                        "Try a more specific query or check network."]),
            error=True,
        )

    # Step 3: summarize via the configured model
    try:
        from openai import OpenAI
        from omnicli.memory import get_config
        from omnicli.auth import get_api_key
        key = get_api_key()
        if key:
            # Prefer the cheaper router model for summarization
            model = ((get_config("router_model", "") or "").strip() or
                     (get_config("main_model", "")   or "").strip() or
                     "gpt-4o-mini")
            base  = ((get_config("router_url", "")   or "").strip() or
                     (get_config("main_url", "")     or "").strip() or None)
            client = OpenAI(api_key=key, base_url=base)
            prompt = (
                f"The user asked: {query!r}\n\n"
                f"Below are {len(scraped)} web pages scraped live. Summarise "
                f"the answer in a professional, well-structured way — include "
                f"specific facts, numbers, names, and dates you find. Cite "
                f"sources by URL at the end. If the pages don't actually "
                f"answer the question, say so honestly.\n\n"
                + "\n\n---\n\n".join(
                    f"[SOURCE {i+1}: {u}]\n{t}" for i, (u, t) in enumerate(scraped)
                )
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000, temperature=0.2,
            )
            summary = (resp.choices[0].message.content or "").strip()
            if summary:
                lines.append("\n" + summary)
                return SlashResult(text="\n".join(lines))
    except Exception as e:
        import logging
        logging.getLogger("omnicli").debug("/web summarize failed: %s", e)

    # Fallback: just list what we scraped
    lines.append("\n(summarizer unavailable — raw excerpts below)\n")
    for u, t in scraped:
        lines.append(f"\n### {u}")
        lines.append(t[:800] + ("…" if len(t) > 800 else ""))
    return SlashResult(text="\n".join(lines))


def _build_default_registry() -> Registry:
    r = Registry()
    for c in (
        SlashCommand("help",    "Show available commands or help for a specific one",
                     _h_help,    usage="/help [command]"),
        SlashCommand("clear",   "Clear the current session",
                     _h_clear),
        SlashCommand("exit",    "Exit the REPL",
                     _h_exit),
        SlashCommand("quit",    "Exit the REPL (alias of /exit)",
                     _h_exit),
        SlashCommand("model",   "Show or set the active model",
                     _h_model,   usage="/model [<model_name>]"),
        SlashCommand("memory",  "Show key config values from memory",
                     _h_memory),
        SlashCommand("perm",    "Manage permission patterns",
                     _h_perm,    usage="/perm list | allow|deny|ask <pattern> | remove <pattern>"),
        SlashCommand("hook",    "Show the hooks config file",
                     _h_hook),
        SlashCommand("session", "Save/load/list sessions",
                     _h_session, usage="/session list | save <name> | load <id> | delete <id>"),
        SlashCommand("compact", "Force context compaction now",
                     _h_compact),
        SlashCommand("cost",    "Show current context token usage",
                     _h_cost),
        SlashCommand("web",     "Search the web and summarise — info-only, no project",
                     _h_web,     usage="/web <query>"),
    ):
        r.register(c)
    _load_user_commands(r)
    return r


# ─── User-defined commands from ~/.phantom/commands/*.md ──────────────────────


_USER_CMDS_DIR = os.path.expanduser("~/.phantom/commands")


def _user_cmds_dir() -> str:
    return os.environ.get("PHANTOM_COMMANDS_DIR", _USER_CMDS_DIR)


def _load_user_commands(reg: Registry) -> int:
    """Scan the user commands dir for *.md files. Each file's basename
    (minus .md) becomes the command name. The file body is the prompt
    template — `{args}` is substituted with the user-supplied args.

    Returns count loaded.
    """
    d = _user_cmds_dir()
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
        description, template = _parse_frontmatter(body)

        def _make_handler(tmpl: str):
            def _h(args: str, ctx: dict) -> SlashResult:
                return SlashResult(
                    text="",
                    rewrite=tmpl.replace("{args}", args.strip()),
                )
            return _h

        reg.register(SlashCommand(
            name=name,
            description=description or f"User command: {name}",
            handler=_make_handler(template),
            is_builtin=False,
        ))
        loaded += 1
    return loaded


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(body: str) -> tuple[str, str]:
    """Extract `description` from YAML frontmatter if present. Returns
    (description, remaining_body). We don't import a YAML library — users
    write simple `key: value` lines and we grep for `description:`."""
    m = _FRONTMATTER_RE.match(body)
    if not m:
        return "", body
    header = m.group(1)
    rest = body[m.end():]
    desc_m = re.search(r"^description:\s*(.*)$", header, re.MULTILINE)
    desc = desc_m.group(1).strip() if desc_m else ""
    # Strip wrapping quotes if present
    if desc.startswith('"') and desc.endswith('"'):
        desc = desc[1:-1]
    if desc.startswith("'") and desc.endswith("'"):
        desc = desc[1:-1]
    return desc, rest.lstrip("\n")


DEFAULT_REGISTRY: Registry = _build_default_registry()


def dispatch(line: str, ctx: Optional[dict] = None) -> SlashResult:
    """Top-level convenience: dispatch against the default registry."""
    return DEFAULT_REGISTRY.dispatch(line, ctx)


def reload_registry() -> Registry:
    """Rebuild the default registry — useful after the user adds new
    ~/.phantom/commands files."""
    global DEFAULT_REGISTRY
    DEFAULT_REGISTRY = _build_default_registry()
    return DEFAULT_REGISTRY


__all__ = [
    "Registry", "SlashCommand", "SlashResult",
    "parse", "dispatch", "DEFAULT_REGISTRY", "reload_registry",
]
