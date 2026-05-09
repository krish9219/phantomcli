"""``phantom chat`` — the REPL that ties everything together.

Wires :class:`phantom.agent.AgentSession` to a TTY: each line you type
becomes a user message; the agent's reply prints back. The session
opens a :class:`MemoryStore` and registers the default tool set so
``run_bash`` / ``memory_add`` / ``memory_search`` work out of the box.

Provider selection: ``--provider openai-compat`` is the default. The
agent talks to any OpenAI-compatible endpoint via ``--base-url`` +
``--api-key`` + ``--model``. The CLI honours these env vars too:

* ``PHANTOM_BASE_URL``
* ``PHANTOM_API_KEY``
* ``PHANTOM_MODEL``

Slash commands inside the REPL:

* ``/exit`` or ``/quit``      — leave.
* ``/reset``                  — clear conversation history (memory persists).
* ``/history``                — print history length.
* ``/help``                   — list commands.

This module implements :func:`chat`, which the top-level Typer app
binds as ``phantom chat``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import typer

from phantom.agent import (
    AgentSession,
    Provider,
    ScriptedProvider,
    default_tools,
)
from phantom.agent.provider import OpenAICompatibleProvider
from phantom.agent.spinner import PhantomSpinner
from phantom.config.providers import CustomProvider, ProviderRegistry
from phantom.errors import PhantomError
from phantom.memory import MemoryStore

__all__ = ["chat", "resolve_chat_config", "run_repl"]


SLASH_COMMANDS = {
    "/exit", "/quit",
    "/reset", "/history", "/help",
    "/model", "/models", "/providers",
    "/add", "/preset", "/presets",
    "/smart",
    "/name", "/workspace",
    "/buy", "/license", "/install-license", "/change-license",
    "/god-mode",
    "/memory",
    "/uninstall",
    "/system", "/doctor",
    "/coder", "/executor", "/dual",
    "/voice", "/dictate",
    "/dashboard",
    "/plugins",
    "/telegram",
}


_CODER_SYSTEM_PROMPT = (
    "You are an expert programmer in a planner/coder role. Another AI "
    "(the executor) will take your output and create the files + run "
    "the commands. So your job is to produce a complete, ready-to-run "
    "implementation as text — no tool calls.\n\n"
    "Format every file like this so the executor can extract it:\n\n"
    "```python file=app.py\n"
    "<full file contents>\n"
    "```\n\n"
    "After the file blocks, list the shell commands the executor "
    "should run, one per line, prefixed with `$`. Example:\n\n"
    "$ pip install flask\n"
    "$ python app.py\n\n"
    "Be complete: include ALL files needed, with their full contents. "
    "Don't say 'omitted for brevity' — write everything out."
)

_EXECUTOR_SYSTEM_PROMPT_PREFIX = (
    "You are the executor in a planner/coder + executor pipeline. A "
    "coder model has already produced the complete implementation as "
    "text below in <coder_plan>...</coder_plan>. Your ONLY job: use "
    "tools to materialise it.\n\n"
    "**ACT, DO NOT NARRATE.** Saying \"I will create app.py\" without "
    "calling write_file is a failure. Saying \"I'll run pip install\" "
    "without calling run_bash is a failure. Skip the description — "
    "go straight to tool calls.\n\n"
    "Specifically:\n"
    "1. For each ```language file=PATH``` block in the plan, call "
    "write_file with that exact path and the block's contents.\n"
    "2. For each `$ command` line in the plan, call run_bash with "
    "that command.\n"
    "3. Do not redesign the code. Do not paste the code in your "
    "reply. Do not summarise file contents. Just write_file + "
    "run_bash, in order.\n"
    "4. Only AFTER every file is written and every command has run, "
    "write a 1–3 sentence summary of what you did.\n\n"
    "<coder_plan>\n"
)
_EXECUTOR_SYSTEM_PROMPT_SUFFIX = "\n</coder_plan>\n"

# Sentinel returned by _handle_slash to mean "exit the REPL".
_SLASH_EXIT = object()


_THINK_BLOCK_RE = None  # populated lazily to keep import-time work small


def _strip_thinking_tags(text: str) -> str:
    """Remove reasoning artefacts that some open-weight models leak.

    Strips both well-formed ``<think>...</think>`` blocks and orphaned
    closing tags like the bare ``</think>`` that llama-3.3 sometimes
    leaves at the start of a reply when the opening tag was emitted
    in a thinking-channel that the API didn't surface.
    """
    if not text:
        return text
    global _THINK_BLOCK_RE
    if _THINK_BLOCK_RE is None:
        import re as _re
        _THINK_BLOCK_RE = _re.compile(
            r"<(think|thinking|thought|reasoning)>.*?</\1>",
            _re.DOTALL | _re.IGNORECASE,
        )
    cleaned = _THINK_BLOCK_RE.sub("", text)

    # Strip orphan closing tags at the start (or anywhere on a line).
    import re as _re
    cleaned = _re.sub(
        r"</(think|thinking|thought|reasoning)>",
        "", cleaned, flags=_re.IGNORECASE,
    )
    # Collapse double newlines created by removing the tags.
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or text  # if stripping wiped everything, keep original


def _looks_garbled(text: str) -> bool:
    """Heuristic: does this look like a broken model output?

    Triggers on the kimi-k2.6 failure mode where the response was a soup of
    pipe characters, multilingual fragments, and tokenizer markers. We're
    intentionally conservative — false positives are worse than missing a
    genuinely weird-but-valid reply.
    """
    if len(text) < 100:
        return False
    sample = text[:1500]
    total = len(sample)
    # A normal English coding answer has very few of these.
    pipe_density = sample.count("|") / total
    backslash_density = sample.count("\\") / total
    # Count CJK / non-Latin runs as a proxy for tokenizer drift.
    non_ascii = sum(1 for c in sample if ord(c) > 127) / total
    return (
        pipe_density > 0.04           # "ed | | answers ing | …" — way over
        or backslash_density > 0.06
        or non_ascii > 0.25
    )


_TOOL_ICONS = {
    "run_bash":      "⚡",
    "start_server":  "🚀",
    "write_file":    "📝",
    "edit_file":     "✏️ ",
    "read_file":     "🔍",
    "list_dir":      "📂",
    "web_search":    "🌐",
    "web_fetch":     "🌍",
    "memory_add":    "💾",
    "memory_search": "🔎",
}


def _tool_icon(name: str) -> str:
    return _TOOL_ICONS.get(name, "→")


def _format_tool_call(name: str, args: dict[str, Any]) -> str:
    """One-line dim summary of a tool call for the live progress feed.

    Picks the most informative arg per tool: command for run_bash,
    path for file tools, query for memory_search. Truncates long
    values so the line stays readable.
    """
    DIM = "\033[2m"; RESET = "\033[0m"
    def _trunc(s: str, n: int = 80) -> str:
        s = " ".join(s.split())  # collapse newlines
        return s if len(s) <= n else s[: n - 1] + "…"

    if name == "run_bash":
        cmd = args.get("command") or args.get("cmd") or ""
        return f"{DIM}{_trunc(str(cmd))}{RESET}"
    if name in ("write_file", "read_file", "list_dir", "edit_file"):
        path = args.get("path", "")
        suffix = ""
        if name == "edit_file":
            old = args.get("old_string", "")
            if isinstance(old, str) and old:
                suffix = f"  {DIM}(replacing {len(old)}b){RESET}"
        return f"{DIM}{_trunc(str(path), 60)}{suffix}{RESET}"
    if name == "memory_search":
        return f"{DIM}query: {_trunc(str(args.get('query', '')), 60)}{RESET}"
    if name == "memory_add":
        return f"{DIM}{_trunc(str(args.get('text', '')), 60)}{RESET}"
    if name == "web_fetch":
        return f"{DIM}{_trunc(str(args.get('url', '')), 80)}{RESET}"
    if name == "web_search":
        return f"{DIM}{_trunc(str(args.get('query', '')), 80)}{RESET}"
    if name == "start_server":
        return f"{DIM}{_trunc(str(args.get('command', '')), 70)}{RESET}"
    keys = list(args.keys())[:3]
    return f"{DIM}({', '.join(keys)}){RESET}"


def _format_tool_result_preview(name: str, result_str: str) -> str:
    """Short dim summary of what a tool returned. Shown right after the
    tool-call line so the user sees progress at a glance.

    Picks the most informative field per tool. Returns "" when there's
    nothing meaningful to show.
    """
    DIM = "\033[2m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; RESET = "\033[0m"
    try:
        data = json.loads(result_str) if isinstance(result_str, str) else {}
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""

    # Errors get a red flag.
    if data.get("error"):
        err = str(data["error"])[:120]
        return f"      {RED}× {err}{RESET}"

    if name == "run_bash":
        rc = data.get("exit_code", 0)
        out = (data.get("stdout") or "").strip().splitlines()
        first = out[0] if out else "(no output)"
        first = first[:100]
        mark = f"{GREEN}✓{RESET}" if rc == 0 else f"{YELLOW}exit {rc}{RESET}"
        return f"      {DIM}{mark} {first}{RESET}"
    if name in ("write_file",):
        b = data.get("bytes_written", 0)
        return f"      {DIM}{GREEN}✓{RESET}{DIM} wrote {b} bytes{RESET}"
    if name == "edit_file":
        diff = data.get("diff", "")
        if diff:
            return _format_diff(diff)
        return f"      {DIM}{GREEN}✓{RESET}{DIM} {data.get('replacements', 0)} replacement(s){RESET}"
    if name == "read_file":
        size = data.get("size_bytes", 0)
        return f"      {DIM}{GREEN}✓{RESET}{DIM} {size} bytes{RESET}"
    if name == "list_dir":
        n = len(data.get("entries") or [])
        return f"      {DIM}{GREEN}✓{RESET}{DIM} {n} entries{RESET}"
    if name == "start_server":
        url = data.get("url", "")
        listening = data.get("listening")
        if listening:
            return f"      {DIM}{GREEN}✓ listening at {url}{RESET}"
        return f"      {DIM}{YELLOW}pid {data.get('pid', '?')}, not yet listening at {url}{RESET}"
    if name == "web_search":
        if isinstance(data, dict) and "error" not in data:
            return ""
        # Empty list etc.
        return ""
    if name == "web_fetch":
        if data.get("ok"):
            status = data.get("status", "?")
            ct = data.get("content_type", "")
            return f"      {DIM}{GREEN}✓{RESET}{DIM} {status} {ct}{RESET}"
    if name == "memory_add":
        rec_id = data.get("id", "")
        return f"      {DIM}{GREEN}✓{RESET}{DIM} stored ({rec_id}){RESET}"
    return ""


def _render_assistant_reply(text: str, write: Callable[[str], None] | None = None) -> None:
    """Render the agent's reply with rich markdown when available.

    Prints a trailing newline. Falls back to plain text via *write* when
    rich isn't installed, the stream isn't a TTY (CI / piped output),
    or the caller passes a custom write target (tests).
    """
    if write is None:
        def _w(s: str):
            sys.stdout.write(s)
            sys.stdout.flush()
        write = _w
    if not text:
        write("\n")
        return
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        if not sys.stdout.isatty():
            raise RuntimeError("not a tty")
        # Rich renders directly to its own stream; we still want the
        # caller's write hook for sequencing. Capture rendered text into
        # a buffer then push through write().
        from io import StringIO
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, soft_wrap=True)
        console.print(Markdown(text))
        write(buf.getvalue() + "\n")
    except Exception:
        write(text + "\n\n")


def _format_diff(diff: str) -> str:
    """Render a unified diff with red/green per line, indented for the
    tool-result preview block."""
    GREEN = "\033[32m"; RED = "\033[31m"; CYAN = "\033[36m"
    DIM = "\033[2m"; RESET = "\033[0m"
    out_lines = []
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            out_lines.append(f"      {DIM}{line}{RESET}")
        elif line.startswith("@@"):
            out_lines.append(f"      {CYAN}{line}{RESET}")
        elif line.startswith("+"):
            out_lines.append(f"      {GREEN}{line}{RESET}")
        elif line.startswith("-"):
            out_lines.append(f"      {RED}{line}{RESET}")
        else:
            out_lines.append(f"      {DIM}{line}{RESET}")
    return "\n".join(out_lines)


_WINDOWS_SHELL_GUIDANCE = (
    "Host OS: Windows. run_bash uses cmd.exe — POSIX commands DO NOT work. "
    "Specifically:\n"
    "- DO NOT use `mkdir -p path` (no -p on Windows). Use the write_file "
    "tool — it auto-creates parent directories. For empty dirs use "
    "`python -c \"import os; os.makedirs(r'C:\\path', exist_ok=True)\"`.\n"
    "- DO NOT pipe to `tail`, `head`, `grep` — those don't exist on Windows. "
    "Use `findstr` for grep-like search.\n"
    "- Path separators: prefer forward slashes (Python and most tools accept "
    "them) or use raw strings with backslashes.\n"
    "- Sequencing: `cmd1 && cmd2` works in cmd.exe. Use `start /b CMD` to "
    "background a server (needed for `python app.py`, `flask run`, etc.)."
)

_POSIX_SHELL_GUIDANCE = (
    "Host OS: {os}. run_bash uses /bin/sh. Standard POSIX is fine "
    "(mkdir -p, &&, pipes, redirection). Background long-running servers "
    "with `nohup CMD >server.log 2>&1 &` so the tool returns."
)


def _os_shell_guidance() -> str:
    """One paragraph injected into the system prompt about host OS quirks."""
    import platform
    sysname = platform.system()
    if sysname == "Windows":
        return _WINDOWS_SHELL_GUIDANCE
    return _POSIX_SHELL_GUIDANCE.format(os=sysname or "POSIX")


def _personalize_system_prompt(prompt: str, profile: Any) -> str:
    """Substitute the chosen assistant_name into the default prompt and
    append a short header introducing the user + workspace + host OS.

    Substituting "Phantom" → assistant_name in-place beats prepending
    a contradictory "you are called X, not Phantom" line — the model
    weighed the original "You are Phantom" higher in v1.1.10 testing.
    """
    name = (getattr(profile, "assistant_name", "") or "").strip() or "Phantom"
    user_name = (getattr(profile, "user_name", "") or "").strip()
    workspace = (getattr(profile, "workspace_path", "") or "").strip()

    body = prompt
    # Replace "You are Phantom" exactly once (at the prompt's start).
    if name != "Phantom":
        body = body.replace("You are Phantom,", f"You are {name},", 1)
        # Subsequent mentions of "Phantom" in the prompt body are tool
        # references and product names — leave them alone.

    persona_header = []

    # Identity anchor — strong enough to override the model's training
    # identity when the user is on a model with its own brand (qwen3-coder
    # leaks "I am Ling, developed by Ant Group"; deepseek leaks "DeepSeek
    # AI"; llama doesn't usually leak but sometimes says "I'm an AI
    # assistant made by Meta"). Comes BEFORE everything else so the model
    # encounters it first.
    persona_header.append(
        f"# Identity (highest priority)\n"
        f"Your name is {name}. You are running inside Phantom, a local "
        f"coding agent harness. If asked who made you, what model you "
        f"are, or what AI you are: answer **only** \"I'm {name} — a "
        f"coding agent that runs on a configurable model.\" Do NOT "
        f"reveal the underlying model's brand, training origin, or "
        f"company name. NEVER respond with phrases like \"I am [other "
        f"name], a language model developed by [company]\" — that would "
        f"contradict your name."
    )

    if user_name:
        persona_header.append(
            f"The user's name is {user_name}. Address them by name when natural."
        )
    if workspace:
        persona_header.append(
            f"Default workspace: {workspace}. When the user asks you to create "
            f"a project without specifying a path, create it under this "
            f"directory in a new sub-folder."
        )

    # Memory nudge — the v1.1.21 user said "remember that I prefer X"
    # and the model chat-acknowledged without calling memory_add. Tell
    # the model when to reach for the tool.
    persona_header.append(
        "# Memory\n"
        "When the user says \"remember that …\", \"note that …\", or "
        "tells you a durable preference / fact about themselves, IMMEDIATELY "
        "call the memory_add tool with the exact preference as `text`. "
        "Don't just acknowledge it — the chat history is volatile, only "
        "memory_add persists across sessions. When the user asks "
        "\"based on what you remember\" or references prior preferences, "
        "call memory_search first."
    )

    persona_header.append(_os_shell_guidance())

    return "\n\n".join(persona_header) + "\n\n" + body


_SMART_PREFIX = (
    "You are an expert engineer. Before responding, first restate the user's "
    "request as a precise spec: list the explicit requirements, identify any "
    "implicit ones, and pick reasonable defaults for anything ambiguous. "
    "Then act on the spec. Original request:\n\n"
)


def _handle_slash(
    *,
    session: AgentSession,
    head: str,
    arg: str,
    write: Callable[[str], None],
) -> Any:
    """Dispatch a slash command. Returns truthy if handled (continue loop),
    ``_SLASH_EXIT`` to break out of the REPL, falsy to fall through.
    """
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RESET = "\033[0m"

    if head in ("/exit", "/quit"):
        return _SLASH_EXIT

    if head == "/reset":
        session.history.clear()
        write(f"{DIM}(history cleared){RESET}\n")
        return True

    if head == "/history":
        write(f"{DIM}(history length: {len(session.history)}){RESET}\n")
        return True

    if head == "/help":
        write(f"{DIM}slash commands:{RESET}\n")
        write(f"  {DIM}── chat ──{RESET}\n")
        write(f"  {CYAN}/reset{RESET} {DIM}— clear conversation history{RESET}\n")
        write(f"  {CYAN}/history{RESET} {DIM}— show history length{RESET}\n")
        write(f"  {CYAN}/exit{RESET} {DIM}— quit (also /quit){RESET}\n")
        write(f"  {DIM}── model ──{RESET}\n")
        write(f"  {CYAN}/model{RESET} {DIM}— show current model{RESET}\n")
        write(f"  {CYAN}/model <name>{RESET} {DIM}— switch to a registered provider{RESET}\n")
        write(f"  {CYAN}/models{RESET} {DIM}— list registered providers (alias /providers){RESET}\n")
        write(f"  {CYAN}/add{RESET} {DIM}— add a new provider via the wizard{RESET}\n")
        write(f"  {CYAN}/preset <name>{RESET} {DIM}— register a curated provider in one step{RESET}\n")
        write(f"  {CYAN}/presets{RESET} {DIM}— list all curated presets (nvidia, openrouter, groq, …){RESET}\n")
        write(f"  {CYAN}/smart [on|off]{RESET} {DIM}— toggle prompt-expansion mode{RESET}\n")
        write(f"  {DIM}── dual-model (planner + executor) ──{RESET}\n")
        write(f"  {CYAN}/coder <provider|model>{RESET} {DIM}— set the coder model{RESET}\n")
        write(f"  {CYAN}/executor <provider|model>{RESET} {DIM}— set the executor (tool-runner) model{RESET}\n")
        write(f"  {CYAN}/dual [on|off]{RESET} {DIM}— toggle two-stage flow{RESET}\n")
        write(f"  {DIM}── you ──{RESET}\n")
        write(f"  {CYAN}/name [new]{RESET} {DIM}— show or rename the assistant{RESET}\n")
        write(f"  {CYAN}/workspace [path]{RESET} {DIM}— show or change project root{RESET}\n")
        write(f"  {CYAN}/system{RESET} {DIM}— show host snapshot (os, ram, disk){RESET}\n")
        write(f"  {CYAN}/memory [query]{RESET} {DIM}— show stored memory; with query, search{RESET}\n")
        write(f"  {CYAN}/god-mode [on|off]{RESET} {DIM}— autonomous mode (default off){RESET}\n")
        write(f"  {DIM}── licence ──{RESET}\n")
        write(f"  {CYAN}/license{RESET} {DIM}— show current tier{RESET}\n")
        write(f"  {CYAN}/buy{RESET} {DIM}— Pro lifetime licence (₹999, 3 devices){RESET}\n")
        write(f"  {CYAN}/install-license <PHC-...>{RESET} {DIM}— activate a key{RESET}\n")
        write(f"  {CYAN}/change-license <PHC-...>{RESET} {DIM}— replace current key{RESET}\n")
        write(f"  {DIM}── tools ──{RESET}\n")
        write(f"  {CYAN}/voice{RESET} {DIM}— voice dictation (Whisper) [Pro]{RESET}\n")
        write(f"  {CYAN}/dashboard{RESET} {DIM}— web UI on :8000 (chat, sessions, plans, costs){RESET}\n")
        write(f"  {CYAN}/doctor{RESET} {DIM}— host capability report (sandbox backends){RESET}\n")
        write(f"  {CYAN}/plugins{RESET} {DIM}— list discovered plugins{RESET}\n")
        write(f"  {CYAN}/telegram{RESET} {DIM}— bridge this agent to a Telegram bot{RESET}\n")
        write(f"  {DIM}── danger ──{RESET}\n")
        write(f"  {CYAN}/uninstall{RESET} {DIM}— remove ~/.phantom/ (asks confirmation){RESET}\n")
        return True

    if head in ("/models", "/providers"):
        registry = ProviderRegistry.load()
        rows = registry.list()
        if not rows:
            write(f"{DIM}(no providers configured — use /add){RESET}\n")
            return True
        current = _current_provider_name(session)
        for p in rows:
            mark = f"{GREEN}*{RESET}" if p.name == current else " "
            default_mark = " (default)" if p.name == registry.default_name else ""
            write(f"  {mark} {p.name:<16} {p.model:<40} {DIM}{p.base_url}{default_mark}{RESET}\n")
        return True

    if head == "/model":
        if not arg:
            write(f"  current model: {CYAN}{getattr(session.provider, '_model', '?')}{RESET}\n")
            write(f"  switch with:  {DIM}/model <provider-name>{RESET}  or  {DIM}/model <model-id>{RESET}\n")
            write(f"  list options: {DIM}/models{RESET}\n")
            return True
        # Take only the first whitespace-delimited token. The v1.1.21 user
        # ran `/model meta/llama-3.3-70b-instruct" then ask "..."` which
        # got registered as a single 60-char "model id" with quotes and
        # narration.
        arg = arg.split()[0].strip("'\"")
        registry = ProviderRegistry.load()
        target = registry.get(arg)
        if target is not None:
            ok = _switch_provider(session, target, write)
            if ok:
                write(f"{GREEN}✓{RESET} switched to {CYAN}{arg}{RESET} ({target.model})\n")
            return True

        # Not a registered provider name — try interpreting as a model id and
        # reuse the current provider's base_url + api_key. Saves the user from
        # re-running /add when they just want to swap the model on the same
        # endpoint (the kimi-k2.6 → llama-3.3-70b-instruct dance).
        provider = getattr(session, "provider", None)
        base_url = getattr(provider, "_base_url", "")
        api_key = getattr(provider, "_api_key", "")
        if base_url and api_key:
            ok = _switch_model_only(session, arg, base_url, api_key, write)
            if ok:
                write(
                    f"{GREEN}✓{RESET} switched model: {DIM}same endpoint,{RESET} "
                    f"{CYAN}{arg}{RESET}\n"
                    f"  {DIM}saved as a new provider for next time. "
                    f"List with /models.{RESET}\n"
                )
                return True

        write(f"{YELLOW}unknown provider or model {arg!r}{RESET}\n")
        names = ", ".join(p.name for p in registry.list()) or "(none)"
        write(f"{DIM}registered providers: {names}{RESET}\n")
        write(f"{DIM}or use: /model <model-id> to swap model on the current endpoint{RESET}\n")
        return True

    if head == "/add":
        from phantom.cli.setup_wizard import run_wizard
        result = run_wizard()
        if not result.cancelled and result.provider is not None:
            write(
                f"{DIM}use it now with:{RESET} "
                f"{CYAN}/model {result.provider.name}{RESET}\n"
            )
        return True

    if head == "/smart":
        flag = arg.strip().lower()
        if flag in ("on", "1", "true", "yes"):
            _set_smart(session, True)
            write(f"{GREEN}✓{RESET} smart mode {DIM}on{RESET} — prompts will be expanded into precise specs.\n")
        elif flag in ("off", "0", "false", "no"):
            _set_smart(session, False)
            write(f"{GREEN}✓{RESET} smart mode {DIM}off{RESET}\n")
        else:
            cur = "on" if _is_smart(session) else "off"
            write(f"  smart mode: {CYAN}{cur}{RESET}  ({DIM}/smart on{RESET} or {DIM}/smart off{RESET})\n")
        return True

    if head == "/name":
        from phantom.profile import load_profile, save_profile
        profile = load_profile()
        if not arg:
            write(f"  assistant: {CYAN}{profile.assistant_name}{RESET}\n")
            write(f"  user:      {CYAN}{profile.user_name or '(not set)'}{RESET}\n")
            write(f"  {DIM}usage: /name <new-assistant-name>{RESET}\n")
            return True
        old = profile.assistant_name
        profile.assistant_name = arg
        save_profile(profile)
        write(f"{GREEN}✓{RESET} assistant renamed: {DIM}{old}{RESET} → {CYAN}{arg}{RESET}\n")
        return True

    if head == "/workspace":
        from phantom.profile import load_profile, save_profile
        from pathlib import Path as _Path
        profile = load_profile()
        if not arg:
            write(f"  workspace: {CYAN}{profile.workspace_path or '(not set)'}{RESET}\n")
            write(f"  {DIM}usage: /workspace <path>{RESET}\n")
            return True
        path = os.path.abspath(os.path.expanduser(arg))
        try:
            _Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            write(f"{YELLOW}could not create {path}: {e}{RESET}\n")
            return True
        profile.workspace_path = path
        save_profile(profile)
        write(f"{GREEN}✓{RESET} workspace set to {CYAN}{path}{RESET}\n")
        return True

    if head == "/system":
        from phantom.cli.sysinfo import collect_system_info
        from phantom.profile import load_profile
        profile = load_profile()
        info = collect_system_info(profile.workspace_path)
        write(f"  {DIM}host:{RESET} {info.hostname}\n")
        write(f"  {DIM}os:{RESET}   {info.os_name} {info.os_release}\n")
        write(f"  {DIM}cpu:{RESET}  {info.cpu} ({info.cpu_count} cores)\n")
        if info.ram_total_gb > 0:
            write(f"  {DIM}ram:{RESET}  {info.ram_free_gb:.1f} GB free of {info.ram_total_gb:.1f} GB\n")
        if info.disk_total_gb > 0:
            write(f"  {DIM}disk:{RESET} {info.disk_free_gb:.1f} GB free of {info.disk_total_gb:.1f} GB\n")
        if profile.workspace_path:
            write(f"  {DIM}workspace:{RESET} {profile.workspace_path}\n")
        return True

    if head == "/buy":
        write(f"  {CYAN}Phantom Pro lifetime licence — ₹999 (one-time, 3 devices){RESET}\n")
        write(f"  {DIM}Buy:    {RESET}https://phantom.aravindlabs.tech/buy\n")
        write(f"  {DIM}After payment your PHC-XXXXXXXX-XXXXXXXX-XXXXXXXX key arrives by email.{RESET}\n")
        write(f"  {DIM}Then run:{RESET} /install-license PHC-...\n")
        return True

    if head in ("/install-license", "/change-license"):
        if not arg:
            write(f"{YELLOW}usage: {head} PHC-XXXXXXXX-XXXXXXXX-XXXXXXXX{RESET}\n")
            write(f"  {DIM}buy a key first: /buy{RESET}\n")
            return True
        ok = _install_license(arg.strip(), write)
        return True

    if head == "/license":
        _show_license_status(write)
        return True

    if head == "/god-mode":
        flag = arg.strip().lower()
        from phantom.profile import load_profile, save_profile
        profile = load_profile()
        if flag in ("on", "1", "true", "yes"):
            profile.god_mode = True
            save_profile(profile)
            _set_god_mode(session, True)
            write(f"{YELLOW}⚡ god-mode active{RESET} — sandbox guards relaxed for this session.\n")
        elif flag in ("off", "0", "false", "no"):
            profile.god_mode = False
            save_profile(profile)
            _set_god_mode(session, False)
            write(f"{GREEN}✓{RESET} god-mode {DIM}off{RESET}\n")
        else:
            cur = "on" if profile.god_mode else "off"
            write(f"  god-mode: {CYAN}{cur}{RESET}  ({DIM}/god-mode on{RESET} or {DIM}/god-mode off{RESET})\n")
        return True

    if head == "/memory":
        _show_memory(arg, write)
        return True

    if head == "/uninstall":
        return _uninstall_flow(arg, write)

    if head == "/coder":
        from phantom.profile import load_profile, save_profile
        profile = load_profile()
        if not arg:
            current = profile.coder_provider or "(not set)"
            write(f"  coder model:    {CYAN}{current}{RESET}\n")
            write(f"  {DIM}usage: /coder <provider-name|model-id>{RESET}\n")
            return True
        ok, name = _resolve_provider_or_model_arg(arg, write)
        if not ok:
            return True
        profile.coder_provider = name
        save_profile(profile)
        write(f"{GREEN}✓{RESET} coder model: {CYAN}{name}{RESET}\n")
        if not profile.dual_mode:
            write(f"  {DIM}enable with: /dual on{RESET}\n")
        return True

    if head == "/executor":
        from phantom.profile import load_profile, save_profile
        profile = load_profile()
        if not arg:
            current = profile.executor_provider or "(not set)"
            write(f"  executor model: {CYAN}{current}{RESET}\n")
            write(f"  {DIM}usage: /executor <provider-name|model-id>{RESET}\n")
            return True
        ok, name = _resolve_provider_or_model_arg(arg, write)
        if not ok:
            return True
        profile.executor_provider = name
        save_profile(profile)
        write(f"{GREEN}✓{RESET} executor model: {CYAN}{name}{RESET}\n")
        if not profile.dual_mode:
            write(f"  {DIM}enable with: /dual on{RESET}\n")
        return True

    if head in ("/preset", "/presets"):
        return _handle_preset(arg, write, head=head)

    if head in ("/voice", "/dictate"):
        return _handle_voice(arg, write)

    if head == "/dashboard":
        return _handle_dashboard(arg, write)

    if head == "/doctor":
        return _handle_doctor(write)

    if head == "/plugins":
        return _handle_plugins(write)

    if head == "/telegram":
        return _handle_telegram(write)

    if head == "/dual":
        from phantom.profile import load_profile, save_profile
        profile = load_profile()
        flag = arg.strip().lower()
        if flag in ("on", "1", "true", "yes"):
            if not profile.coder_provider or not profile.executor_provider:
                write(f"{YELLOW}set /coder and /executor first{RESET}\n")
                write(f"  {DIM}coder    : {profile.coder_provider or '(not set)'}{RESET}\n")
                write(f"  {DIM}executor : {profile.executor_provider or '(not set)'}{RESET}\n")
                return True
            profile.dual_mode = True
            save_profile(profile)
            write(f"{GREEN}✓{RESET} dual mode {DIM}on{RESET}\n")
            write(f"  {DIM}coder    →{RESET} {CYAN}{profile.coder_provider}{RESET}\n")
            write(f"  {DIM}executor →{RESET} {CYAN}{profile.executor_provider}{RESET}\n")
        elif flag in ("off", "0", "false", "no"):
            profile.dual_mode = False
            save_profile(profile)
            write(f"{GREEN}✓{RESET} dual mode {DIM}off{RESET}\n")
        else:
            cur = "on" if profile.dual_mode else "off"
            write(f"  dual mode:  {CYAN}{cur}{RESET}\n")
            write(f"  coder:      {CYAN}{profile.coder_provider or '(not set)'}{RESET}\n")
            write(f"  executor:   {CYAN}{profile.executor_provider or '(not set)'}{RESET}\n")
            write(f"  {DIM}toggle: /dual on  |  /dual off{RESET}\n")
        return True

    return False


def _handle_preset(arg: str, write: Callable[[str], None], *, head: str = "/preset") -> bool:
    """`/preset <name>` registers a curated provider in one step. Asks
    interactively for the API key. `/presets` (plural) lists them."""
    DIM = "\033[2m"; CYAN = "\033[36m"; GREEN = "\033[32m"
    YELLOW = "\033[33m"; RESET = "\033[0m"
    from phantom.config.presets import PRESETS, get_preset
    if head == "/presets" or (head == "/preset" and not arg):
        write(f"  {DIM}available presets:{RESET}\n")
        for p in PRESETS:
            free_hint = ""
            if p.name in ("nvidia", "groq", "openrouter", "github"):
                free_hint = f"  {DIM}(free tier){RESET}"
            elif p.name in ("ollama", "lmstudio", "vllm-local"):
                free_hint = f"  {DIM}(local, no key){RESET}"
            write(f"    {CYAN}{p.name:<12}{RESET} {p.model:<48}{free_hint}\n")
        write(f"  {DIM}usage: /preset <name>{RESET}\n")
        return True

    preset = get_preset(arg.strip())
    if preset is None:
        write(f"{YELLOW}unknown preset: {arg!r}{RESET}\n")
        write(f"  {DIM}list with /presets{RESET}\n")
        return True

    api_key = ""
    if preset.name not in ("ollama", "lmstudio", "vllm-local"):
        existing_env = os.environ.get(preset.api_key_env, "")
        if existing_env:
            write(f"  {DIM}using {preset.api_key_env} from environment{RESET}\n")
        else:
            try:
                from typer import prompt as _typer_prompt
                api_key = _typer_prompt(
                    f"  paste API key for {preset.name} (Enter to set ${preset.api_key_env} later)",
                    default="", show_default=False,
                ).strip()
            except Exception:
                api_key = ""

    registry = ProviderRegistry.load()
    candidate = preset.name
    n = 2
    while registry.get(candidate) is not None:
        candidate = f"{preset.name}-{n}"
        n += 1
        if n > 99:
            break
    try:
        registry.add(CustomProvider(
            name=candidate,
            base_url=preset.base_url,
            model=preset.model,
            api_key_env=preset.api_key_env,
            api_key_inline=api_key,
        ))
    except ValueError as e:
        write(f"  failed: {e}\n")
        return True
    write(f"{GREEN}✓{RESET} registered preset {CYAN}{candidate}{RESET} → {preset.model}\n")
    write(f"  {DIM}use it with:{RESET} /model {candidate}\n")
    return True


def _handle_voice(arg: str, write: Callable[[str], None]) -> bool:
    DIM = "\033[2m"; YELLOW = "\033[33m"; CYAN = "\033[36m"; RESET = "\033[0m"
    write(f"  {CYAN}phantom voice / dictate{RESET}\n")
    write(f"  {DIM}records audio and transcribes via Whisper.{RESET}\n")
    write(f"  {DIM}this is a Pro feature; run from a fresh terminal:{RESET}\n")
    write(f"\n    {CYAN}phantom dictate{RESET}\n\n")
    write(f"  {DIM}requires:{RESET} sox / arecord / parecord on PATH, plus faster-whisper.\n")
    write(f"  {DIM}status:{RESET} run {CYAN}/license{RESET} to check Pro tier.\n")
    if arg:
        write(f"\n  {YELLOW}note:{RESET} `/voice` doesn't accept arguments inside chat — dictation runs as a separate command.\n")
    return True


def _handle_dashboard(arg: str, write: Callable[[str], None]) -> bool:
    DIM = "\033[2m"; CYAN = "\033[36m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RESET = "\033[0m"
    write(f"  {CYAN}phantom dashboard{RESET}\n")
    write(f"  {DIM}web UI on http://127.0.0.1:8000 — chat, sessions, plans, costs, plugins.{RESET}\n")
    write(f"\n  start it from a fresh terminal so it doesn't block this REPL:\n")
    write(f"\n    {CYAN}phantom dashboard{RESET}\n")
    write(f"    {CYAN}phantom dashboard --port 9000{RESET}        {DIM}# alternate port{RESET}\n")
    write(f"    {CYAN}phantom dashboard --base-url ... --model ...{RESET}  {DIM}# wire a provider{RESET}\n")
    return True


def _handle_doctor(write: Callable[[str], None]) -> bool:
    """Inline a compact host capability report."""
    DIM = "\033[2m"; CYAN = "\033[36m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RESET = "\033[0m"
    try:
        from phantom.cli.doctor import build_report
    except Exception as e:
        write(f"{YELLOW}doctor module unavailable: {e}{RESET}\n")
        return True
    try:
        report = build_report()
    except Exception as e:
        write(f"{YELLOW}doctor build failed: {e}{RESET}\n")
        return True
    selected = report.get("selected") or "(none)"
    write(f"  {DIM}sandbox:{RESET}  {CYAN}{selected}{RESET}\n")
    backends = report.get("backends") or []
    for entry in backends:
        if isinstance(entry, dict):
            name = entry.get("name", "?")
            ok = bool(entry.get("available"))
        else:
            name, ok = str(entry), True
        mark = f"{GREEN}✓{RESET}" if ok else f"{DIM}—{RESET}"
        write(f"    {mark} {name}\n")
    if report.get("selected") is None:
        write(f"  {YELLOW}no sandbox backend available — running in passthrough mode (no isolation){RESET}\n")
    return True


def _handle_telegram(write: Callable[[str], None]) -> bool:
    """Surface the existing v3 Telegram bot infrastructure."""
    DIM = "\033[2m"; CYAN = "\033[36m"; YELLOW = "\033[33m"; RESET = "\033[0m"
    write(f"  {CYAN}phantom telegram{RESET}\n")
    write(f"  {DIM}long-running bot that bridges this agent to a Telegram chat.{RESET}\n")
    write(f"\n  {DIM}setup (one time):{RESET}\n")
    write(f"    1. talk to {CYAN}@BotFather{RESET} on Telegram, run {DIM}/newbot{RESET}, "
          f"copy the token\n")
    write(f"    2. set the env var (Windows PowerShell):\n")
    write(f"       {DIM}[Environment]::SetEnvironmentVariable(\"TELEGRAM_BOT_TOKEN\", "
          f"\"123:abc...\", \"User\"){RESET}\n")
    write(f"    3. open a fresh terminal, run:\n")
    write(f"       {CYAN}phantom telegram{RESET}    {DIM}# starts the bot, blocks{RESET}\n")
    write(f"\n  {DIM}features:{RESET}\n")
    write(f"    • {CYAN}/start{RESET} {DIM}, {RESET}{CYAN}/help{RESET}, "
          f"{CYAN}/memories{RESET}, {CYAN}/clear{RESET}, {CYAN}/auto{RESET}\n")
    write(f"    • full chat to your Phantom from your phone\n")
    write(f"    • per-chat-id session — you and other users get isolated context\n")
    write(f"  {YELLOW}note:{RESET} other social channels (Slack, Discord, WhatsApp) "
          f"aren't built yet; add them as plugins or open an issue.\n")
    return True


def _handle_plugins(write: Callable[[str], None]) -> bool:
    DIM = "\033[2m"; CYAN = "\033[36m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RESET = "\033[0m"
    try:
        from phantom.plugins.loader import PluginLoader
        from phantom.plugins.registry import PluginRegistry
        loaded = PluginLoader().discover()
        registry = PluginRegistry.load()
    except Exception as e:
        write(f"{YELLOW}plugin layer unavailable: {e}{RESET}\n")
        return True
    if not loaded:
        write(f"  {DIM}no plugins discovered{RESET}\n")
        write(f"  {DIM}install one with:{RESET} {CYAN}phantom plugin install <name>{RESET}\n")
        return True
    write(f"  {DIM}{'NAME':<16}{'VERSION':<10}{'ENABLED':<9}{'CAPABILITIES'}{RESET}\n")
    for p in loaded:
        caps = ",".join(sorted(c.value for c in p.manifest.capabilities)) or "-"
        enabled = "yes" if registry.is_enabled(p.manifest.name) else "no"
        mark = f"{GREEN}{enabled:<9}{RESET}" if enabled == "yes" else f"{DIM}{enabled:<9}{RESET}"
        write(f"    {p.manifest.name:<16}{p.manifest.version:<10}{mark}{caps}\n")
    return True


def _resolve_provider_or_model_arg(arg: str, write: Callable[[str], None]) -> tuple[bool, str]:
    """For /coder + /executor: arg can be a registered provider name OR a
    raw model id. If a model id, register it as a new provider on the
    current default's endpoint+key first. Returns (ok, provider_name)."""
    YELLOW = "\033[33m"; DIM = "\033[2m"; RESET = "\033[0m"
    registry = ProviderRegistry.load()
    if registry.get(arg) is not None:
        return True, arg

    # Treat as a model id — clone the current default's endpoint+key.
    default = registry.get_default()
    if default is None:
        write(f"{YELLOW}no providers configured. Run /add first.{RESET}\n")
        return False, ""

    api_key = ""
    if default.api_key_env:
        api_key = os.environ.get(default.api_key_env, "")
    if not api_key:
        api_key = default.api_key_inline
    if not api_key:
        write(f"{YELLOW}default provider has no API key — can't reuse it. Run /add for {arg}.{RESET}\n")
        return False, ""

    # Build a synthetic name from the model id.
    import re as _re
    last = arg.rsplit("/", 1)[-1] or arg
    last = _re.sub(r"[^a-z0-9_-]", "-", last.lower()).strip("-") or "custom"
    if not _re.match(r"^[a-z]", last):
        last = "m-" + last
    last = last[:30] or "custom"

    candidate = last
    n = 2
    while registry.get(candidate) is not None:
        candidate = f"{last}-{n}"
        n += 1
        if n > 99:
            break
    try:
        registry.add(
            CustomProvider(
                name=candidate,
                base_url=default.base_url,
                model=arg,
                api_key_inline=api_key,
            ),
        )
    except ValueError as exc:
        write(f"{YELLOW}failed to register {arg!r}: {exc}{RESET}\n")
        return False, ""
    write(f"  {DIM}registered new provider: {candidate} → {arg}{RESET}\n")
    return True, candidate


def _install_license(key: str, write: Callable[[str], None]) -> bool:
    GREEN = "\033[32m"; YELLOW = "\033[33m"; DIM = "\033[2m"; RESET = "\033[0m"
    try:
        from phantom import licensing
        result = licensing.activate(key)
    except Exception as e:
        write(f"{YELLOW}licence activation failed: {e}{RESET}\n")
        return False
    if not getattr(result, "valid", False):
        reason = getattr(result, "reason", "")
        write(f"{YELLOW}licence rejected: {reason or 'unknown'}{RESET}\n")
        return False
    write(f"{GREEN}✓{RESET} licence installed.\n")
    _show_license_status(write)
    return True


def _show_license_status(write: Callable[[str], None]) -> None:
    CYAN = "\033[36m"; DIM = "\033[2m"; RESET = "\033[0m"
    try:
        from phantom import licensing
        s = licensing.license_status()
    except Exception as e:
        write(f"  could not read licence: {e}\n")
        return
    write(f"  tier:    {CYAN}{s.tier}{RESET}\n")
    if s.email:
        write(f"  email:   {s.email}\n")
    if s.tier == "trial" and s.days_remaining is not None:
        write(f"  trial:   {s.days_remaining} day(s) remaining\n")
    write(f"  {DIM}buy upgrade:{RESET} https://phantom.aravindlabs.tech/buy\n")


def _show_memory(arg: str, write: Callable[[str], None]) -> None:
    CYAN = "\033[36m"; DIM = "\033[2m"; YELLOW = "\033[33m"; RESET = "\033[0m"
    try:
        from phantom.memory import MemoryStore
        phantom_home = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
        store = MemoryStore.open(Path(phantom_home) / "memory.db")
    except Exception as e:
        write(f"{YELLOW}memory store unavailable: {e}{RESET}\n")
        return
    try:
        if arg.strip():
            results = store.search(query=arg.strip(), limit=10) if hasattr(store, "search") else []
            if not results:
                write(f"  {DIM}no matches for {arg!r}{RESET}\n")
                return
            for i, r in enumerate(results, start=1):
                text = getattr(r, "text", str(r))[:120]
                write(f"  {CYAN}{i}.{RESET} {text}\n")
        else:
            count = 0
            try:
                count = store.count() if hasattr(store, "count") else 0
            except Exception:
                pass
            write(f"  memory:  {CYAN}{count}{RESET} entries at {DIM}~/.phantom/memory.db{RESET}\n")
            write(f"  {DIM}search:  /memory <query>{RESET}\n")
            write(f"  {DIM}store:   the agent calls memory_add/memory_search automatically.{RESET}\n")
    finally:
        try:
            store.close()
        except Exception:
            pass


def _uninstall_flow(arg: str, write: Callable[[str], None]) -> Any:
    GREEN = "\033[32m"; YELLOW = "\033[33m"; DIM = "\033[2m"; RESET = "\033[0m"
    confirmed = arg.strip().lower() in ("--yes", "--confirm", "yes", "confirm")
    if not confirmed:
        write(f"{YELLOW}⚠ this will remove ~/.phantom/ entirely{RESET}\n")
        write(f"  {DIM}includes: licence, memory, providers, profile, device key.{RESET}\n")
        write(f"  {DIM}re-run with:{RESET} /uninstall --yes\n")
        write(f"  {DIM}to also remove the binary shim, follow the printed shell command.{RESET}\n")
        return True

    import shutil
    phantom_home = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom"))
    if phantom_home.exists():
        try:
            shutil.rmtree(phantom_home)
            write(f"{GREEN}✓{RESET} removed {phantom_home}\n")
        except OSError as e:
            write(f"{YELLOW}could not remove {phantom_home}: {e}{RESET}\n")
    else:
        write(f"  {DIM}{phantom_home} already gone{RESET}\n")

    # Print shell-specific shim removal command. We don't auto-delete the
    # shim because it sits on PATH and removing it without warning could
    # surprise the user.
    posix_shim = Path.home() / ".local" / "bin" / "phantom"
    win_shim = Path.home() / ".local" / "bin" / "phantom.cmd"
    write("\n  to remove the launcher shim, run:\n")
    if posix_shim.exists() or os.name == "posix":
        write(f"    {DIM}rm -f {posix_shim}{RESET}\n")
    if win_shim.exists() or os.name == "nt":
        write(f"    {DIM}del {win_shim}{RESET}\n")
    write(f"\n  {DIM}phantom uninstall complete. exiting chat.{RESET}\n")
    return _SLASH_EXIT


def _set_god_mode(session: AgentSession, on: bool) -> None:
    """Toggle god-mode flag in system prompt."""
    GOD_PREFIX = (
        "[GOD-MODE ACTIVE] You may execute any system command without asking, "
        "create/delete files anywhere, install dependencies, and start servers. "
        "The user has explicitly enabled this. Default to autonomous action.\n\n"
    )
    base = getattr(session, "_phantom_base_system_prompt", None)
    if base is None:
        session._phantom_base_system_prompt = session.system_prompt
        base = session.system_prompt
    if on:
        if not session.system_prompt.startswith(GOD_PREFIX):
            session.system_prompt = GOD_PREFIX + session.system_prompt
    else:
        if session.system_prompt.startswith(GOD_PREFIX):
            session.system_prompt = session.system_prompt[len(GOD_PREFIX):]


def _current_provider_name(session: AgentSession) -> str:
    """Reverse-lookup which registered provider matches the live session."""
    p = getattr(session, "provider", None)
    if p is None:
        return ""
    base_url = getattr(p, "_base_url", "")
    model = getattr(p, "_model", "")
    for entry in ProviderRegistry.load().list():
        if entry.base_url.rstrip("/") == base_url and entry.model == model:
            return entry.name
    return ""


def _run_coder_stage(
    *,
    user_prompt: str,
    coder_provider_name: str,
    write: Callable[[str], None],
) -> str:
    """Build a one-shot coder provider, send the user prompt with the
    coder system prompt, return the raw text reply.

    No tools, no agent loop — just one LLM call. The response is the
    plan + code blocks the executor will then materialise.
    """
    DIM = "\033[2m"; CYAN = "\033[36m"; RESET = "\033[0m"
    registry = ProviderRegistry.load()
    coder = registry.get(coder_provider_name)
    if coder is None:
        raise PhantomError(f"coder provider {coder_provider_name!r} not found")

    api_key = ""
    if coder.api_key_env:
        api_key = os.environ.get(coder.api_key_env, "")
    if not api_key:
        api_key = coder.api_key_inline

    coder_provider = OpenAICompatibleProvider(
        base_url=coder.base_url,
        api_key=api_key,
        model=coder.model,
        # Coder doesn't need tools — and many strong-coder models have
        # broken tool-call formats anyway. Disabling sidesteps the issue.
        tools_supported=False,
    )

    from phantom.agent.provider import ProviderMessage
    messages = [
        ProviderMessage(role="system", content=_CODER_SYSTEM_PROMPT),
        ProviderMessage(role="user", content=user_prompt),
    ]
    write(f"\r\033[K  {DIM}[coder: {coder.model}] thinking…{RESET}\n")
    response = coder_provider.complete(messages, tools=[])
    text = response.text or ""
    if not text.strip():
        raise PhantomError("coder returned empty response")
    # Show a short preview so the user knows the coder did something.
    preview = text[:200].replace("\n", " ")
    if len(text) > 200:
        preview += "…"
    write(f"  {DIM}[coder draft: {len(text)} chars]{RESET} {preview}\n")
    return text


def _switch_model_only(
    session: AgentSession,
    model_id: str,
    base_url: str,
    api_key: str,
    write: Callable[[str], None],
) -> bool:
    """Swap just the model on the active provider's endpoint+key, register a
    new entry in the provider registry so the user can /model it next time.

    Auto-derives the new entry's name from the model id (last path segment
    cleaned, or fallback to "custom"). If the derived name collides we
    append -2, -3, etc.
    """
    import re as _re
    try:
        new_provider = OpenAICompatibleProvider(
            base_url=base_url, api_key=api_key, model=model_id,
        )
    except PhantomError as exc:
        write(f"  failed: {exc.detail or exc}\n")
        return False

    if hasattr(new_provider, "set_tools_warning_sink"):
        new_provider.set_tools_warning_sink(lambda msg: write(f"\r{msg}\n"))
    session.provider = new_provider
    session.history = [m for m in session.history if m.role != "tool"]

    # Register the new entry for future /model calls.
    last = model_id.rsplit("/", 1)[-1] or model_id
    last = _re.sub(r"[^a-z0-9_-]", "-", last.lower()).strip("-") or "custom"
    if not _re.match(r"^[a-z]", last):
        last = "m-" + last
    last = last[:30] or "custom"

    registry = ProviderRegistry.load()
    candidate = last
    n = 2
    while registry.get(candidate) is not None:
        candidate = f"{last}-{n}"
        n += 1
        if n > 99:
            break
    try:
        registry.add(
            CustomProvider(
                name=candidate, base_url=base_url, model=model_id,
                api_key_inline=api_key,
            ),
            overwrite=False,
        )
    except ValueError:
        pass  # collision after race; harmless
    return True


def _switch_provider(
    session: AgentSession,
    target: CustomProvider,
    write: Callable[[str], None],
) -> bool:
    """Rebuild ``session.provider`` against *target*. Latches off tools by
    default for the new provider — the next call will probe via the
    fallback if the model actually rejects."""
    api_key = ""
    if target.api_key_env:
        api_key = os.environ.get(target.api_key_env, "")
    if not api_key:
        api_key = target.api_key_inline

    try:
        new_provider = OpenAICompatibleProvider(
            base_url=target.base_url,
            api_key=api_key,
            model=target.model,
        )
    except PhantomError as exc:
        write(f"  failed: {exc.detail or exc}\n")
        return False

    if hasattr(new_provider, "set_tools_warning_sink"):
        new_provider.set_tools_warning_sink(lambda msg: write(f"\r{msg}\n"))
    session.provider = new_provider
    # Drop tool residue so the new model doesn't choke on orphan tool turns.
    session.history = [m for m in session.history if m.role != "tool"]
    return True


def _set_smart(session: AgentSession, on: bool) -> None:
    """Toggle smart-mode by mutating the system prompt prefix."""
    base = getattr(session, "_phantom_base_system_prompt", None)
    if base is None:
        # First toggle: remember the original system prompt.
        session._phantom_base_system_prompt = session.system_prompt
        base = session.system_prompt
    if on:
        session.system_prompt = _SMART_PREFIX + base
    else:
        session.system_prompt = base


def _is_smart(session: AgentSession) -> bool:
    return session.system_prompt.startswith(_SMART_PREFIX)


def _build_provider(
    *,
    name: str,
    base_url: str,
    api_key: str,
    model: str,
) -> Provider:
    """Construct the provider the user asked for. Currently only one
    flavour ships; the function exists so future stages can branch on
    ``name``.
    """
    if name == "openai-compat":
        return OpenAICompatibleProvider(
            base_url=base_url, api_key=api_key, model=model, name=name,
        )
    if name == "scripted":
        # For testing the REPL itself.
        return ScriptedProvider()
    raise PhantomError(f"unknown provider {name!r}")


def run_repl(
    session: AgentSession,
    *,
    read_line: Callable[[], str] | None = None,
    write: Callable[[str], None] | None = None,
) -> int:
    """Drive *session* against an input source. Returns the exit code.

    Both ``read_line`` and ``write`` default to ``sys.stdin.readline`` /
    ``sys.stdout.write``; tests pass deterministic substitutes.
    """
    if read_line is None:
        # prompt_toolkit gives us multi-line paste detection (bracketed
        # paste mode) + Tab completion + Alt+Enter for explicit newlines.
        # Fall back to plain sys.stdin.readline on non-TTY.
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.key_binding import KeyBindings
        except Exception:
            def _read():
                return sys.stdin.readline()
            read_line = _read
        else:
            history_path = Path(
                os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
            ) / ".chat_history"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            chat_completer = WordCompleter(
                sorted(SLASH_COMMANDS),
                ignore_case=True,
                sentence=False,
            )
            chat_bindings = KeyBindings()

            @chat_bindings.add("escape", "enter")
            def _alt_enter(event):
                event.current_buffer.insert_text("\n")

            @chat_bindings.add("enter")
            def _enter(event):
                event.current_buffer.validate_and_handle()

            # Bracketed-paste handler: prompt_toolkit handles paste events
            # natively in multi-line mode, so we don't need a custom Ctrl+V
            # binding. The visual `[Pasted: N lines]` summary is printed
            # below, AFTER the user submits, so the buffer stays clean
            # without needing terminal-trickery during input.

            try:
                _chat_session = PromptSession(
                    history=FileHistory(str(history_path)),
                    auto_suggest=AutoSuggestFromHistory(),
                    completer=chat_completer,
                    complete_while_typing=False,
                    multiline=True,
                    key_bindings=chat_bindings,
                    enable_open_in_editor=True,
                    mouse_support=False,
                )
                # Capture the styled label closure so the prompt-toolkit
                # session owns the rendering (avoids the v1.1.24 bug
                # where multiline-mode repaints clobbered the label
                # printed before read_line).
                CYAN = "\033[36m"; RESET = "\033[0m"
                def _build_prompt_label():
                    try:
                        return f"\n{CYAN}{user_label} ›{RESET} "
                    except Exception:
                        return f"\n{user_label} > "

                def _read():
                    try:
                        text = _chat_session.prompt(_build_prompt_label())
                    except (EOFError, KeyboardInterrupt):
                        return ""
                    # Show paste-indicator AFTER the user submits if the
                    # message was multi-line. Prints to stderr so it
                    # doesn't pollute stdout and gets recorded above the
                    # spinner. Does NOT alter the submitted text.
                    if text and text.count("\n") >= 2:
                        n_lines = text.count("\n") + 1
                        n_chars = len(text)
                        sys.stdout.write(
                            f"\033[2m[Pasted: {n_lines} lines, {n_chars} chars]\033[0m\n"
                        )
                        sys.stdout.flush()
                    return text + "\n"
                read_line = _read
                # Mark that the prompt label is owned by prompt-toolkit
                # now; run_repl shouldn't write its own copy.
                read_line.__phantom_owns_label__ = True  # type: ignore[attr-defined]
            except Exception:
                def _read():
                    return sys.stdin.readline()
                read_line = _read
    if write is None:
        def _write(s: str):
            sys.stdout.write(s)
            sys.stdout.flush()
        write = _write

    # Cyan/dim ANSI helpers — same palette as the spinner.
    CYAN = "\033[36m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    RESET = "\033[0m"

    # If the underlying provider is OpenAICompatibleProvider, give it a sink
    # so the user sees inline notice when tools are auto-disabled.
    provider = getattr(session, "provider", None)
    if hasattr(provider, "set_tools_warning_sink"):
        provider.set_tools_warning_sink(lambda msg: write(f"\r{msg}\n"))

    # The boot banner already printed when chat() called us — only print the
    # plain greeting when run_repl is invoked directly (tests, embedded use).
    if getattr(session, "_phantom_already_greeted", False) is False:
        write(f"\n  {CYAN}Phantom{RESET} {DIM}— local AI agent. /help for commands, /exit to quit.{RESET}\n\n")

    # Personalised prompts: use the user's name (if any) instead of "you",
    # and the assistant's chosen name instead of literal "phantom".
    from phantom.profile import load_profile as _load_profile
    user_label = "you"
    assistant_label = "phantom"
    try:
        _prof = _load_profile()
        if _prof.user_name:
            user_label = _prof.user_name
        if _prof.assistant_name:
            assistant_label = _prof.assistant_name.lower()
    except Exception:
        pass
    label_owned = getattr(read_line, "__phantom_owns_label__", False)
    while True:
        if not label_owned:
            write(f"{CYAN}{user_label} ›{RESET} ")
        line = read_line()
        if not line:
            # EOF (Ctrl-D / pipe closed): exit gracefully.
            write("\n")
            return 0
        prompt = line.rstrip("\n")
        if not prompt:
            continue

        # Slash commands accept arguments: `/model llama-3.3-70b-instruct`.
        head, _, tail = prompt.partition(" ")
        if head in SLASH_COMMANDS:
            handled = _handle_slash(
                session=session,
                head=head,
                arg=tail.strip(),
                write=write,
            )
            if handled is _SLASH_EXIT:
                return 0
            if handled:
                continue
            # Unhandled slash falls through to LLM. Shouldn't normally happen.

        spinner = PhantomSpinner()
        spinner.start()

        # Dual-model: pre-stage the user's prompt with a coder pass.
        try:
            from phantom.profile import load_profile as _lp
            _prof_dual = _lp()
        except Exception:
            _prof_dual = None
        effective_prompt = prompt
        prior_system_prompt = None
        if (
            _prof_dual is not None
            and _prof_dual.dual_mode
            and _prof_dual.coder_provider
            and _prof_dual.executor_provider
        ):
            try:
                spinner.set_phase("routing")
                coder_output = _run_coder_stage(
                    user_prompt=prompt,
                    coder_provider_name=_prof_dual.coder_provider,
                    write=write,
                )
                spinner.set_phase("executing")
                # The executor prompt is a *system prompt* swap for this
                # turn — putting it in the user message let the model
                # treat the directive as content (it described instead
                # of executing). Restore after respond_to() returns.
                prior_system_prompt = session.system_prompt
                session.system_prompt = (
                    _EXECUTOR_SYSTEM_PROMPT_PREFIX
                    + coder_output
                    + _EXECUTOR_SYSTEM_PROMPT_SUFFIX
                )
            except Exception as exc:
                spinner.stop(mark="✗")
                write(f"{DIM}coder stage failed:{RESET} {exc}\n")
                write(f"  {DIM}falling through to single-model mode for this turn{RESET}\n")
                spinner = PhantomSpinner()
                spinner.start()

        try:
            reply = session.respond_to(effective_prompt)
        except KeyboardInterrupt:
            if prior_system_prompt is not None:
                session.system_prompt = prior_system_prompt
            # Ctrl+C during a turn — abort cleanly, keep the REPL alive.
            spinner.stop(mark="✗")
            write(f"{DIM}(interrupted — partial state kept; press Ctrl+C again to exit){RESET}\n")
            continue
        except PhantomError as exc:
            spinner.stop(mark="✗")
            write(f"{DIM}error:{RESET} {exc.detail or exc}\n")
            continue
        except Exception as exc:
            spinner.stop(mark="✗")
            write(f"{DIM}error:{RESET} {exc}\n")
            continue
        spinner.stop()
        if prior_system_prompt is not None:
            session.system_prompt = prior_system_prompt
        reply = _strip_thinking_tags(reply)
        # Render assistant reply with rich markdown (code blocks get
        # syntax highlighting, lists indent, tables align, **bold**
        # works). Falls back to plain text on non-TTY or when rich
        # isn't available.
        write(f"{GREEN}{assistant_label} ›{RESET} ")
        _render_assistant_reply(reply, write=write)
        if _looks_garbled(reply):
            current_model = getattr(session.provider, "_model", "")
            write(
                f"\n  \033[33m⚠{RESET} that reply looks garbled "
                f"({DIM}model {current_model}{RESET}). The model may be "
                f"misbehaving on this endpoint. Try:\n"
                f"  {DIM}/reset{RESET} then {DIM}/model meta/llama-3.3-70b-instruct{RESET}\n"
            )


def resolve_chat_config(
    *, base_url: str, api_key: str, model: str,
) -> tuple[str, str, str, CustomProvider | None]:
    """Resolve the (base_url, api_key, model) trio for chat.

    Order of precedence:
      1. Explicit ``--base-url`` / ``--model`` (or ``PHANTOM_*`` env vars)
         that Typer has already coerced into the args.
      2. The saved default provider in ``providers.json``. Its API key
         comes from the registered env var (or the inline key, if any).
      3. ``(None, None, None, None)`` — caller should run the setup wizard.
    """
    if base_url and model:
        return base_url, api_key, model, None

    registry = ProviderRegistry.load()
    default = registry.get_default()
    if default is None:
        return base_url, api_key, model, None

    resolved_key = api_key
    if not resolved_key:
        if default.api_key_env:
            resolved_key = os.environ.get(default.api_key_env, "")
        if not resolved_key and default.api_key_inline:
            resolved_key = default.api_key_inline

    return default.base_url, resolved_key, default.model, default


def chat(
    base_url: str = typer.Option(
        "", "--base-url",
        envvar="PHANTOM_BASE_URL",
        help="OpenAI-compatible API base URL.",
    ),
    api_key: str = typer.Option(
        "", "--api-key",
        envvar="PHANTOM_API_KEY",
        help="API key. May be empty for local providers (Ollama, vLLM).",
    ),
    model: str = typer.Option(
        "", "--model",
        envvar="PHANTOM_MODEL",
        help="Model identifier. e.g. 'gpt-4o-mini' or 'meta/llama-3.3-70b-instruct'.",
    ),
    provider_name: str = typer.Option(
        "openai-compat", "--provider", help="Provider flavour.",
    ),
    workdir: str = typer.Option(
        "", "--workdir", "-w", help="Workdir for run_bash. Default: cwd.",
    ),
    user: str = typer.Option(
        "default", "--user", help="Memory namespace: user.",
    ),
    project: str = typer.Option(
        "default", "--project", help="Memory namespace: project.",
    ),
    session_id: str = typer.Option(
        "default", "--session", help="Memory namespace: session.",
    ),
    no_memory: bool = typer.Option(
        False, "--no-memory", help="Disable the memory tools entirely.",
    ),
) -> None:
    """Start an interactive chat session with the agent."""
    if (base_url and not model) or (model and not base_url):
        typer.echo(
            "phantom chat: --base-url and --model must be set together "
            "(or set PHANTOM_BASE_URL / PHANTOM_MODEL).",
            err=True,
        )
        raise typer.Exit(2)

    base_url, api_key, model, _ = resolve_chat_config(
        base_url=base_url, api_key=api_key, model=model,
    )

    if not base_url or not model:
        from phantom.cli.setup_wizard import run_wizard, should_run_wizard
        if should_run_wizard(base_url=base_url, model=model):
            result = run_wizard()
            if result.cancelled or result.provider is None:
                raise typer.Exit(2)
            base_url, _, model, _ = resolve_chat_config(
                base_url="", api_key="", model="",
            )
            if not api_key:
                p = result.provider
                api_key = (
                    os.environ.get(p.api_key_env, "") if p.api_key_env else ""
                ) or p.api_key_inline
        else:
            typer.echo(
                "phantom chat: no provider configured. Run `phantom chat` "
                "interactively to set one up, or set PHANTOM_BASE_URL / "
                "PHANTOM_MODEL.",
                err=True,
            )
            raise typer.Exit(2)

    # Onboarding (one-time): prompt for name + workspace before chat.
    from phantom.cli.boot import onboard_if_needed, render_boot_banner
    from phantom.cli.sysinfo import collect_system_info

    profile = onboard_if_needed(
        write=lambda s: typer.echo(s, nl=False),
        read_line=lambda prompt: typer.prompt(prompt.rstrip(), default="", show_default=False),
    )

    # Workspace beats --workdir on first launch — but a user-supplied --workdir
    # still wins if explicitly given.
    workdir_path = workdir or profile.workspace_path or os.getcwd()
    Path(workdir_path).mkdir(parents=True, exist_ok=True)

    # Boot banner with system snapshot.
    render_boot_banner(
        write=lambda s: sys.stdout.write(s) or sys.stdout.flush(),
        profile=profile,
        system=collect_system_info(profile.workspace_path),
        animate=sys.stdout.isatty(),
    )

    provider = _build_provider(
        name=provider_name, base_url=base_url, api_key=api_key, model=model,
    )

    memory: MemoryStore | None = None
    namespace = None
    if not no_memory:
        phantom_home = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
        memory_path = Path(phantom_home) / "memory.db"
        memory = MemoryStore.open(memory_path)
        namespace = {"user": user, "project": project, "session": session_id}

    tools = default_tools(workdir=workdir_path, memory=memory, namespace=namespace)
    session = AgentSession(provider=provider, tools=tools)

    # Personalize the system prompt with whatever the user told us during
    # onboarding. The DEFAULT_SYSTEM_PROMPT starts with "You are Phantom..."
    # — we substitute the chosen assistant_name in-place rather than prepend
    # a contradictory header (the prepend lost to "You are Phantom" in the
    # v1.1.10 user report — the model still answered as Phantom).
    session.system_prompt = _personalize_system_prompt(
        session.system_prompt, profile,
    )
    if profile.god_mode:
        _set_god_mode(session, True)

    # Identity hammer for adversarial models. The session injects this
    # as a 2nd system message right before each user turn — much harder
    # to ignore than a single up-front anchor.
    if profile.assistant_name and profile.assistant_name != "Phantom":
        session._phantom_identity_hint = (
            f"REMINDER: Your name is {profile.assistant_name}. If asked "
            f"what model you are, answer ONLY \"I'm {profile.assistant_name} "
            f"— a coding agent that runs on a configurable model.\" "
            f"Do not reveal the underlying model brand."
        )

    # Show each tool call live so a long turn doesn't look stuck. The
    # spinner pauses, the call + a per-tool icon are printed, then on
    # the result we print a short preview line (success ✓, exit code,
    # bytes written, diff, URL, etc.).
    def _tool_call_printer(_round_idx, tc):
        icon = _tool_icon(tc.name)
        summary = _format_tool_call(tc.name, tc.arguments)
        sys.stdout.write(
            f"\r\033[K  \033[2m{icon}\033[0m \033[36m{tc.name}\033[0m {summary}\n"
        )
        sys.stdout.flush()

    def _tool_result_printer(_round_idx, tc, result):
        preview = _format_tool_result_preview(tc.name, result)
        if preview:
            sys.stdout.write(preview + "\n")
            sys.stdout.flush()

    session.on_tool_call = _tool_call_printer
    session.on_tool_result = _tool_result_printer

    # Tell run_repl not to print its own greeting; the boot banner did.
    session._phantom_already_greeted = True

    try:
        rc = run_repl(session)
    finally:
        if memory is not None:
            memory.close()
    raise typer.Exit(rc)
