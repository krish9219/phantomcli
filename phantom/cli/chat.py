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

import os
import sys
from pathlib import Path
from typing import Callable

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
    "/add",
    "/smart",
    "/name", "/workspace",
    "/buy", "/license", "/install-license", "/change-license",
    "/god-mode",
    "/memory",
    "/uninstall",
    "/system",
}

# Sentinel returned by _handle_slash to mean "exit the REPL".
_SLASH_EXIT = object()


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
    keys = list(args.keys())[:3]
    return f"{DIM}({', '.join(keys)}){RESET}"


def _personalize_system_prompt(prompt: str, profile: Any) -> str:
    """Substitute the chosen assistant_name into the default prompt and
    append a short header introducing the user + workspace.

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
    if user_name:
        persona_header.append(
            f"The user's name is {user_name}. Address them by name when natural; "
            f"do not call yourself 'Phantom' if your name is not Phantom."
        )
    if workspace:
        persona_header.append(
            f"Default workspace: {workspace}. When the user asks you to create "
            f"a project without specifying a path, create it under this "
            f"directory in a new sub-folder."
        )

    if persona_header:
        return "\n".join(persona_header) + "\n\n" + body
    return body


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
        write(f"  {CYAN}/smart [on|off]{RESET} {DIM}— toggle prompt-expansion mode{RESET}\n")
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
            write(f"  switch with:  {DIM}/model <provider-name>{RESET}\n")
            write(f"  list options: {DIM}/models{RESET}\n")
            return True
        registry = ProviderRegistry.load()
        target = registry.get(arg)
        if target is None:
            write(f"{YELLOW}unknown provider {arg!r}{RESET}\n")
            names = ", ".join(p.name for p in registry.list()) or "(none)"
            write(f"{DIM}registered: {names}{RESET}\n")
            return True
        ok = _switch_provider(session, target, write)
        if ok:
            write(f"{GREEN}✓{RESET} switched to {CYAN}{arg}{RESET} ({target.model})\n")
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

    return False


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
    while True:
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
        try:
            reply = session.respond_to(prompt)
        except KeyboardInterrupt:
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
        write(f"{GREEN}{assistant_label} ›{RESET} {reply}\n\n")


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

    # Show each tool call live so a long turn doesn't look stuck. The
    # spinner pauses, the call is printed, then the spinner resumes —
    # all driven from the tool callback the session calls between
    # rounds.
    def _tool_call_printer(_round_idx, tc):
        summary = _format_tool_call(tc.name, tc.arguments)
        sys.stdout.write(f"\r\033[K  \033[2m→\033[0m \033[36m{tc.name}\033[0m {summary}\n")
        sys.stdout.flush()

    session.on_tool_call = _tool_call_printer

    # Tell run_repl not to print its own greeting; the boot banner did.
    session._phantom_already_greeted = True

    try:
        rc = run_repl(session)
    finally:
        if memory is not None:
            memory.close()
    raise typer.Exit(rc)
