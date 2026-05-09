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
}

# Sentinel returned by _handle_slash to mean "exit the REPL".
_SLASH_EXIT = object()


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
        write(f"  {CYAN}/model{RESET} {DIM}— show current model{RESET}\n")
        write(f"  {CYAN}/model <name>{RESET} {DIM}— switch to a registered provider{RESET}\n")
        write(f"  {CYAN}/models{RESET} {DIM}— list registered providers (alias /providers){RESET}\n")
        write(f"  {CYAN}/add{RESET} {DIM}— add a new provider via the wizard{RESET}\n")
        write(f"  {CYAN}/smart [on|off]{RESET} {DIM}— toggle prompt-expansion mode{RESET}\n")
        write(f"  {CYAN}/reset{RESET} {DIM}— clear conversation history{RESET}\n")
        write(f"  {CYAN}/history{RESET} {DIM}— show history length{RESET}\n")
        write(f"  {CYAN}/help{RESET} {DIM}— this list{RESET}\n")
        write(f"  {CYAN}/exit{RESET} {DIM}— quit (also /quit){RESET}\n")
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

    return False


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

    write(f"\n  {CYAN}Phantom{RESET} {DIM}— local AI agent. /help for commands, /exit to quit.{RESET}\n\n")
    while True:
        write(f"{CYAN}you ›{RESET} ")
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
        except PhantomError as exc:
            spinner.stop(mark="✗")
            write(f"{DIM}error:{RESET} {exc.detail or exc}\n")
            continue
        except Exception as exc:
            spinner.stop(mark="✗")
            write(f"{DIM}error:{RESET} {exc}\n")
            continue
        spinner.stop()
        write(f"{GREEN}phantom ›{RESET} {reply}\n\n")


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

    workdir_path = workdir or os.getcwd()
    Path(workdir_path).mkdir(parents=True, exist_ok=True)

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

    try:
        rc = run_repl(session)
    finally:
        if memory is not None:
            memory.close()
    raise typer.Exit(rc)
