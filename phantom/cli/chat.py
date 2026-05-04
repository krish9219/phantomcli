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
from phantom.errors import PhantomError
from phantom.memory import MemoryStore

__all__ = ["chat", "run_repl"]


SLASH_COMMANDS = {"/exit", "/quit", "/reset", "/history", "/help"}


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

    write("Phantom — local AI agent. Type /help for commands, /exit to quit.\n")
    while True:
        write("you> ")
        line = read_line()
        if not line:
            # EOF (Ctrl-D / pipe closed): exit gracefully.
            write("\n")
            return 0
        prompt = line.rstrip("\n")
        if not prompt:
            continue
        if prompt in SLASH_COMMANDS:
            if prompt in ("/exit", "/quit"):
                return 0
            if prompt == "/reset":
                session.history.clear()
                write("(history cleared)\n")
                continue
            if prompt == "/history":
                write(f"(history length: {len(session.history)})\n")
                continue
            if prompt == "/help":
                write("Slash commands: " + ", ".join(sorted(SLASH_COMMANDS)) + "\n")
                continue
        try:
            reply = session.respond_to(prompt)
        except PhantomError as exc:
            write(f"error: {exc.detail or exc}\n")
            continue
        write(f"phantom> {reply}\n")


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
    if not base_url or not model:
        typer.echo(
            "phantom chat: --base-url and --model are required "
            "(or set PHANTOM_BASE_URL / PHANTOM_MODEL).",
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
