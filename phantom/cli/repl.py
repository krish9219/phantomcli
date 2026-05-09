"""Phantom shell — interactive REPL that dispatches into the existing Typer app.

Triggered by running ``phantom`` with no subcommand. Each line is parsed with
``shlex.split`` and dispatched as if the user had typed ``phantom <line>``,
so every existing subcommand works inside the shell without re-typing
``phantom``.

prompt_toolkit is preferred (history, line editing, Ctrl+R search). When it's
unavailable or the host isn't a TTY (e.g. piped input in CI), we fall back
to plain ``input()`` so tests and scripts still work.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typer

__all__ = ["run_repl"]

BUILTINS = {"exit", "quit", ":q", "help", "?", "clear", "cls"}


class _ChatBridge:
    """Lazy-built AgentSession that the REPL routes plain-text messages to.

    First plain-text line in a session triggers:
      1. ``resolve_chat_config()`` — pulls base_url/model from saved default.
      2. Setup wizard if nothing's saved (only on TTY).
      3. AgentSession creation, cached for the rest of the REPL lifetime.
    """

    def __init__(self):
        self._session = None
        self._memory = None

    def respond(self, prompt: str) -> tuple[str, str | None]:
        """Returns (reply, error). On error, reply is "" and error is set."""
        if self._session is None:
            err = self._build()
            if err:
                return "", err
        from phantom.agent.spinner import PhantomSpinner
        spinner = PhantomSpinner()
        spinner.start()
        try:
            reply = self._session.respond_to(prompt)
        except Exception as exc:
            spinner.stop(mark="✗")
            return "", str(exc)
        spinner.stop()
        return reply, None

    def _build(self) -> str | None:
        from phantom.agent import AgentSession, default_tools
        from phantom.cli.chat import resolve_chat_config, _build_provider
        from phantom.cli.setup_wizard import run_wizard, should_run_wizard
        from phantom.memory import MemoryStore

        base_url, api_key, model, default = resolve_chat_config(
            base_url="", api_key="", model="",
        )
        if not (base_url and model):
            if not sys.stdin.isatty():
                return (
                    "no provider configured. Run `phantom chat` on a TTY to "
                    "set one up, or `phantom config provider preset <name>`."
                )
            if not should_run_wizard(base_url=base_url, model=model):
                return "no provider configured."
            result = run_wizard()
            if result.cancelled or result.provider is None:
                return "setup cancelled."
            base_url, api_key, model, default = resolve_chat_config(
                base_url="", api_key="", model="",
            )
            if not api_key and default is not None:
                api_key = (
                    os.environ.get(default.api_key_env, "")
                    if default.api_key_env else ""
                ) or default.api_key_inline

        try:
            provider = _build_provider(
                name="openai-compat", base_url=base_url, api_key=api_key, model=model,
            )
        except Exception as exc:
            return str(exc)

        if hasattr(provider, "set_tools_warning_sink"):
            provider.set_tools_warning_sink(lambda m: sys.stderr.write(f"{m}\n"))

        phantom_home = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
        memory_path = Path(phantom_home) / "memory.db"
        try:
            self._memory = MemoryStore.open(memory_path)
            namespace = {"user": "default", "project": "default", "session": "default"}
            tools = default_tools(workdir=os.getcwd(), memory=self._memory, namespace=namespace)
        except Exception:
            self._memory = None
            tools = default_tools(workdir=os.getcwd())

        self._session = AgentSession(provider=provider, tools=tools)
        return None

    def close(self) -> None:
        if self._memory is not None:
            try:
                self._memory.close()
            except Exception:
                pass
            self._memory = None


def _banner(license_tier: str, license_detail: str, version: str) -> str:
    GREEN = "\033[1;32m"
    CYAN = "\033[1;36m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    if license_tier == "pro":
        tier_str = f"{GREEN}Pro{RESET}"
    elif license_tier == "trial":
        tier_str = f"{CYAN}Pro · trial{RESET}"
    else:
        tier_str = f"\033[1;33mFree{RESET}"

    return (
        f"\n  Phantom v{version}  ·  {tier_str}{('  ' + license_detail) if license_detail else ''}\n"
        f"  {DIM}Type {RESET}help{DIM} to list commands, {RESET}exit{DIM} to quit.{RESET}\n"
    )


def _click_exits():
    """Return the click exception classes that mean "command finished, no error".

    In click 8+ ``Exit`` and ``Abort`` are RuntimeError subclasses, not
    SystemExit, so a plain ``except SystemExit`` doesn't catch them.
    """
    from click.exceptions import Abort, Exit
    return (Exit, Abort)

def _click_usage_error():
    from click.exceptions import UsageError
    return UsageError


def _show_help(app) -> None:
    """Invoke `phantom --help` style listing without exiting the loop."""
    from typer.main import get_command
    cmd = get_command(app)
    try:
        cmd(args=["--help"], standalone_mode=False)
    except (SystemExit, *_click_exits()):
        pass
    except Exception as e:
        msg = str(e).strip()
        if msg:
            print(f"(help failed: {msg})", file=sys.stderr)


def _dispatch(app, argv: list[str]) -> None:
    """Run a single subcommand without letting SystemExit / click.Exit kill the REPL."""
    from typer.main import get_command
    cmd = get_command(app)
    try:
        cmd(args=argv, standalone_mode=False)
    except (SystemExit, *_click_exits()):
        # --help, no_args_is_help, and clean command exits all raise here.
        pass
    except KeyboardInterrupt:
        print("(interrupted)", file=sys.stderr)
    except _click_usage_error() as e:
        # "No such command", "Missing argument", etc. — pretty-print, stay alive.
        print(e.format_message(), file=sys.stderr)
    except Exception as e:
        msg = str(e).strip()
        if msg:
            print(f"error: {msg}", file=sys.stderr)


def _make_prompt(history_file: Path):
    """Return a callable that reads one line. Uses prompt_toolkit if possible."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        history_file.parent.mkdir(parents=True, exist_ok=True)
        session = PromptSession(
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
        )
        return lambda: session.prompt("phantom> ")
    except Exception:
        return lambda: input("phantom> ")


def _known_commands(app) -> set[str]:
    """The set of subcommand names the top-level Typer app exposes."""
    from typer.main import get_command
    cmd = get_command(app)
    return set(getattr(cmd, "commands", {}).keys())


def run_repl() -> None:
    """Entry point. Opens the shell, dispatches each line, returns on exit."""
    from phantom._version import __version__
    from phantom.licensing import license_status
    from phantom.cli import app  # imported here to avoid circular import at module-load time

    s = license_status()
    detail = ""
    if s.tier == "trial" and s.days_remaining is not None:
        detail = f"\033[2m· {s.days_remaining}d remaining\033[0m"
    elif s.tier == "pro" and s.email:
        detail = f"\033[2m· {s.email}\033[0m"

    sys.stderr.write(_banner(s.tier, detail, __version__))
    sys.stderr.flush()

    history_file = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")) / ".repl_history"
    read_line = _make_prompt(history_file)

    chat_bridge = _ChatBridge()
    commands = _known_commands(app)

    try:
        while True:
            try:
                line = read_line()
            except EOFError:
                print()
                return
            except KeyboardInterrupt:
                # Ctrl+C at the prompt — clear the line, stay in the loop
                continue

            line = line.strip()
            if not line:
                continue

            if line in {"exit", "quit", ":q"}:
                return
            if line in {"help", "?"}:
                _show_help(app)
                continue
            if line in {"clear", "cls"}:
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
                continue

            try:
                argv = shlex.split(line)
            except ValueError as e:
                print(f"parse error: {e}", file=sys.stderr)
                continue

            head = argv[0]
            if head in commands or head.startswith("-"):
                _dispatch(app, argv)
                continue

            reply, err = chat_bridge.respond(line)
            if err:
                print(f"error: {err}", file=sys.stderr)
                continue
            sys.stdout.write(reply.rstrip() + "\n")
            sys.stdout.flush()
    finally:
        chat_bridge.close()
