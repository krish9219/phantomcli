"""Phantom v4 CLI — Typer entry point.

Stages 1-2 ship: ``phantom doctor``, ``phantom run``, ``phantom version``,
and ``phantom plugin {list,enable,disable}``. The full agent CLI lands
in later stages.

Subcommands are defined on this module's :data:`app` directly. Each
subcommand's implementation can live in a sibling module (``doctor.py``,
``run.py``) but the binding stays here so Typer sees a clean function
signature with all default Option(...) markers intact.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

import typer

from phantom._version import __version__
from phantom.cli.chat import chat as _chat_impl
from phantom.cli.doctor import build_report, _print_text_report
from phantom.engine.executor import (
    ExecuteBashRequest,
    ExecuteBashResult,
    execute_bash,
)
from phantom.errors import (
    PermissionDeniedError,
    SandboxLaunchError,
    SandboxTimeoutError,
)
from phantom.plugins.loader import PluginLoader
from phantom.plugins.registry import PluginRegistry
from phantom.sandbox.policy import ResourceLimits

__all__ = ["app", "main"]


app: typer.Typer = typer.Typer(
    name="phantom",
    help="Phantom — local AI agent. Run with no args to open an interactive shell.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    """When the user runs `phantom` with no subcommand, drop into the shell."""
    if ctx.invoked_subcommand is None:
        from phantom.cli.repl import run_repl
        run_repl()

plugin_app: typer.Typer = typer.Typer(
    name="plugin",
    help="Manage Phantom plugins.",
    no_args_is_help=True,
)
app.add_typer(plugin_app, name="plugin")

auth_app: typer.Typer = typer.Typer(
    name="auth",
    help="OAuth login for AI providers — GitHub Models (free tier), Google, Anthropic, OpenAI.",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")

mcp_app: typer.Typer = typer.Typer(
    name="mcp",
    help="Host or talk to MCP (Model Context Protocol) servers.",
    no_args_is_help=True,
)
app.add_typer(mcp_app, name="mcp")


# ─── auth subcommand bindings ─────────────────────────────────────────────────
from phantom.cli.auth import login as _auth_login
from phantom.cli.auth import logout as _auth_logout
from phantom.cli.auth import status as _auth_status
from phantom.cli.auth import whoami as _auth_whoami

auth_app.command("login", help="Start a device-code OAuth login. github = free GPT-4o + Claude.")(_auth_login)
auth_app.command("logout", help="Forget local tokens for a provider.")(_auth_logout)
auth_app.command("status", help="Show which providers have valid local tokens.")(_auth_status)
auth_app.command("whoami", help="Show identity info for a logged-in provider (currently github).")(_auth_whoami)


# ─── mcp serve binding ────────────────────────────────────────────────────────
@mcp_app.command("serve", help="Run a stdio MCP server exposing Phantom's tools.")
def _mcp_serve_cmd(
    workdir: str = typer.Option(
        "", "--workdir", "-w",
        help="Workspace dir for sandbox + file tools. Default: cwd.",
    ),
) -> None:
    """Run a Model Context Protocol server over stdio.

    Configure your MCP client (Claude Desktop, ChatGPT Desktop, the
    `mcp` CLI) to spawn `phantom mcp serve`. The server exposes
    run_bash, web_fetch, read_file, write_file, list_dir as MCP tools.
    """
    from phantom.cli.mcp_serve import build_default_mcp_server, serve_stdio

    final_workdir = workdir or os.getcwd()
    os.makedirs(final_workdir, exist_ok=True)
    server = build_default_mcp_server(workdir=final_workdir)
    serve_stdio(server)


@app.command(name="version", help="Print Phantom's version and exit.")
def _version_cmd() -> None:
    typer.echo(__version__)


@app.command(name="doctor", help="Show host capability report.")
def _doctor_cmd(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON."
    ),
) -> None:
    import json
    report = build_report()
    if json_output:
        typer.echo(json.dumps(report, separators=(",", ":")))
    else:
        _print_text_report(report)
    if report["selected"] is None:
        raise typer.Exit(1)


@app.command(
    name="run",
    help="Run a command inside the sandbox. Use `--` to separate phantom flags from the command.",
    context_settings={"allow_extra_args": False, "ignore_unknown_options": True},
)
def _run_cmd(
    args: List[str] = typer.Argument(
        ..., help="Command and its arguments. Example: phantom run -- echo hi"
    ),
    workdir: Optional[str] = typer.Option(
        None, "--workdir", "-w",
        help="Working directory inside the sandbox. Defaults to the current dir.",
    ),
    network: bool = typer.Option(
        False, "--network",
        help="Enable network in the sandbox (default: disabled).",
    ),
    wall_s: float = typer.Option(
        300.0, "--wall-s", help="Wall-clock deadline in seconds."
    ),
    cpu_s: float = typer.Option(
        60.0, "--cpu-s", help="CPU-time ceiling in seconds (≤ wall_s)."
    ),
    rss_mib: int = typer.Option(
        512, "--rss-mib", help="RSS ceiling in MiB."
    ),
) -> None:
    if not args:
        typer.echo("phantom run: nothing to execute", err=True)
        raise typer.Exit(2)

    workdir_resolved = workdir or os.getcwd()
    os.makedirs(workdir_resolved, exist_ok=True)

    cmd = " ".join(_quote_for_sh(a) for a in args)

    try:
        req = ExecuteBashRequest(
            command=cmd,
            workdir=workdir_resolved,
            writable_paths=(workdir_resolved,),
            network=network,
            limits=ResourceLimits(
                wall_s=wall_s,
                cpu_s=min(cpu_s, wall_s),
                rss_mib=rss_mib,
            ),
            original_argv=tuple(args),
        )
        result: ExecuteBashResult = execute_bash(req)
    except PermissionDeniedError as exc:
        typer.echo(f"phantom run: blocked: {exc.detail}", err=True)
        raise typer.Exit(126) from exc
    except SandboxTimeoutError as exc:
        typer.echo(f"phantom run: timeout after {exc.deadline_s}s", err=True)
        raise typer.Exit(124) from exc
    except SandboxLaunchError as exc:
        typer.echo(f"phantom run: launch failed: {exc.detail}", err=True)
        raise typer.Exit(125) from exc

    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.truncated:
        sys.stderr.write("\n[phantom run: output was truncated]\n")
    raise typer.Exit(result.exit_code)


def _quote_for_sh(arg: str) -> str:
    """Quote *arg* safely for /bin/sh -c.

    Single-quote everything; replace embedded single quotes with the
    standard ``'\\''`` escape.
    """
    return "'" + arg.replace("'", "'\\''") + "'"


app.command(name="chat", help="Start an interactive chat session.")(_chat_impl)


@app.command(
    name="login",
    help="Shortcut for `phantom auth login --provider <name>`. "
         "Default: github (free GPT-4o + Claude 3.5 Sonnet).",
)
def _login_shortcut(
    provider: str = typer.Argument(
        "github",
        help="OAuth provider: github | google | anthropic | openai",
    ),
    poll_interval_s: float = typer.Option(
        0.0, "--interval",
        help="Override polling interval (seconds).",
    ),
    max_minutes: float = typer.Option(
        5.0, "--max-minutes",
        help="Give up after this many minutes if the user hasn't authorised.",
    ),
) -> None:
    """Run ``phantom auth login --provider <provider>`` directly."""
    _auth_login(
        provider=provider,
        poll_interval_s=poll_interval_s,
        max_minutes=max_minutes,
    )


@app.command(
    name="logout",
    help="Shortcut for `phantom auth logout --provider <name>`.",
)
def _logout_shortcut(
    provider: str = typer.Argument("github", help="OAuth provider name."),
) -> None:
    """Run ``phantom auth logout --provider <provider>`` directly."""
    _auth_logout(provider=provider)


@app.command(
    name="whoami",
    help="Shortcut for `phantom auth whoami` (default: github).",
)
def _whoami_shortcut(
    provider: str = typer.Option(
        "github", "--provider", "-p",
        help="Which provider to identify against. Currently: github.",
    ),
) -> None:
    """Run ``phantom auth whoami`` directly."""
    _auth_whoami(provider=provider)


@app.command(name="dashboard", help="Start the web dashboard.")
def _dashboard_cmd(
    host: str = typer.Option("127.0.0.1", "--host",
                              help="Bind address. Loopback by default."),
    port: int = typer.Option(8000, "--port", help="TCP port to bind."),
    base_url: str = typer.Option(
        "", "--base-url", envvar="PHANTOM_BASE_URL",
        help="OpenAI-compatible base URL (optional).",
    ),
    api_key: str = typer.Option(
        "", "--api-key", envvar="PHANTOM_API_KEY",
        help="API key for the provider (optional).",
    ),
    model: str = typer.Option(
        "", "--model", envvar="PHANTOM_MODEL",
        help="Model identifier (optional).",
    ),
) -> None:
    """Start the web dashboard.

    Without --base-url / --model, the dashboard runs in echo mode
    (every user message gets a hint to wire a provider). With them
    set, every conversation calls the provider with the default
    sandbox + plugin tool set.
    """
    import uvicorn

    from phantom.dashboard import build_app

    if host not in ("127.0.0.1", "localhost") and \
            os.environ.get("PHANTOM_DASHBOARD_ALLOW_NON_LOOPBACK", "") != "1":
        typer.echo(
            f"Refusing to bind {host!r} without explicit consent; "
            "set PHANTOM_DASHBOARD_ALLOW_NON_LOOPBACK=1 to override.",
            err=True,
        )
        raise typer.Exit(2)

    cfg = _build_dashboard_config(
        base_url=base_url, api_key=api_key, model=model,
    )
    app_obj = build_app(cfg)
    typer.echo(f"Phantom dashboard listening on http://{host}:{port}")
    uvicorn.run(app_obj, host=host, port=port, log_level="warning")


def _build_dashboard_config(
    *, base_url: str, api_key: str, model: str,
):
    """Wire a DashboardConfig with the user's provider when supplied."""
    from phantom.dashboard import DashboardConfig

    if not (base_url and model):
        return DashboardConfig()

    from phantom.agent import AgentSession, default_tools
    from phantom.agent.provider import OpenAICompatibleProvider

    def factory():
        provider = OpenAICompatibleProvider(
            base_url=base_url, api_key=api_key, model=model,
        )
        return AgentSession(
            provider=provider,
            tools=default_tools(workdir=os.getcwd()),
        )

    def plugins():
        from phantom.plugins.loader import PluginLoader
        from phantom.plugins.registry import PluginRegistry
        loaded = PluginLoader().discover()
        registry = PluginRegistry.load()
        return [
            {
                "name": p.manifest.name,
                "version": p.manifest.version,
                "capabilities": [c.value for c in p.manifest.capabilities],
                "enabled": registry.is_enabled(p.manifest.name),
                "signed": p.signed,
            }
            for p in loaded
        ]

    return DashboardConfig(
        session_factory=factory,
        plugin_provider=plugins,
    )


@plugin_app.command("list", help="List discovered plugins and their enabled state.")
def _plugin_list() -> None:
    loader = PluginLoader()
    registry = PluginRegistry.load()
    plugins = loader.discover()
    if not plugins:
        typer.echo("No plugins found.")
        raise typer.Exit(0)
    typer.echo(f"{'NAME':<16}{'VERSION':<10}{'ENABLED':<9}{'SIGNED':<8}CAPABILITIES")
    for p in plugins:
        caps = ",".join(sorted(c.value for c in p.manifest.capabilities)) or "-"
        enabled = "yes" if registry.is_enabled(p.manifest.name) else "no"
        signed = "yes" if p.signed else "no"
        typer.echo(
            f"{p.manifest.name:<16}{p.manifest.version:<10}{enabled:<9}{signed:<8}{caps}"
        )


@plugin_app.command("enable", help="Enable a plugin by name.")
def _plugin_enable(name: str = typer.Argument(..., help="Plugin name.")) -> None:
    registry = PluginRegistry.load()
    registry.enable(name)
    typer.echo(f"plugin {name!r} enabled")


@plugin_app.command("search", help="Search the plugin mirror.")
def _plugin_search(
    query: str = typer.Argument("", help="text to search; empty = list all"),
    mirror: Optional[str] = typer.Option(None, "--mirror", help="override mirror URL"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    from phantom.plugins.mirror import MirrorClient, MirrorError
    import json as _json
    client = MirrorClient(mirror) if mirror else MirrorClient()
    try:
        idx = client.index()
    except MirrorError as e:
        typer.echo(f"mirror error: {e}", err=True)
        raise typer.Exit(1)
    matches = idx.search(query)
    if json_output:
        typer.echo(_json.dumps(
            [{"name": p.name, "version": p.version, "description": p.description}
             for p in matches], indent=2,
        ))
        return
    if not matches:
        typer.echo("(no matches)")
        return
    for p in matches:
        desc = (p.description[:60] + "…") if len(p.description) > 60 else p.description
        typer.echo(f"  {p.name:<20} {p.version:<10} {desc}")


@plugin_app.command("install", help="Install a plugin from the mirror.")
def _plugin_install(
    name: str = typer.Argument(..., help="plugin name"),
    version: Optional[str] = typer.Option(None, "--version", help="pin a version (default: latest)"),
    mirror: Optional[str] = typer.Option(None, "--mirror", help="override mirror URL"),
    require_signature: bool = typer.Option(False, "--require-signed", help="refuse unsigned bundles"),
    force: bool = typer.Option(False, "--force", help="overwrite existing install"),
) -> None:
    from phantom.plugins.mirror import MirrorClient, MirrorError
    client = MirrorClient(mirror) if mirror else MirrorClient()
    try:
        target = client.install(
            name, version=version,
            require_signature=require_signature, force=force,
        )
    except MirrorError as e:
        typer.echo(f"install failed: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"installed {name} → {target}")


@plugin_app.command("uninstall", help="Remove an installed plugin.")
def _plugin_uninstall(name: str = typer.Argument(..., help="plugin name")) -> None:
    from phantom.plugins.mirror import MirrorClient
    if MirrorClient().uninstall(name):
        typer.echo(f"uninstalled {name}")
    else:
        typer.echo(f"not installed: {name}", err=True)
        raise typer.Exit(1)


@plugin_app.command("publish", help="(Operator) Build + register a plugin in a mirror store.")
def _plugin_publish(
    plugin_dir: str = typer.Argument(..., help="path to a plugin source dir with manifest.json"),
    store: str = typer.Option(..., "--store", help="mirror store directory"),
    public_key: str = typer.Option("", "--public-key", help="base64 ed25519 public key"),
) -> None:
    from phantom.plugins.mirror.server import MirrorStore
    s = MirrorStore(Path(store))
    entry = s.publish(Path(plugin_dir), public_key_b64=public_key)
    typer.echo(f"published {entry['name']} {entry['version']} ({entry['sha256'][:12]}…)")


@plugin_app.command("disable", help="Disable a plugin by name.")
def _plugin_disable(name: str = typer.Argument(..., help="Plugin name.")) -> None:
    registry = PluginRegistry.load()
    registry.disable(name)
    typer.echo(f"plugin {name!r} disabled")


# ─── v1.0 commands: bench / serve / connect / swarm / self-dev / dictate ─────
# Wired here so they're discoverable via `phantom --help` without further
# plumbing in run.py or any other entry point. Each lives in its own module
# under phantom/cli/* to keep this file from ballooning.

from phantom.cli.bench import bench as _bench_impl
from phantom.cli.dictate_cmd import dictate_cmd as _dictate_impl
from phantom.cli.license_cmd import license_app as _license_app
from phantom.cli.memory_cmd import memory_app as _memory_app
from phantom.cli.mcp_import_cmd import mcp_import as _mcp_import_impl, mcp_import_dry as _mcp_import_dry_impl
from phantom.cli.provider_cmd import config_app as _config_app
from phantom.cli.selfdev_cmd import selfdev_cmd as _selfdev_impl
from phantom.cli.swarm_cmd import swarm_cmd as _swarm_impl

app.command("bench", help="Run reproducible performance benchmarks.")(_bench_impl)
app.command("dictate", help="Record audio and transcribe via Whisper. [Pro]")(_dictate_impl)
app.command("swarm", help="Fan out N subagents into isolated git worktrees. [Pro]")(_swarm_impl)
app.command("self-dev", help="Apply a change in a sandboxed worktree, run tests. [Pro]")(_selfdev_impl)
app.add_typer(_license_app, name="license")
app.add_typer(_memory_app, name="memory")
app.add_typer(_config_app, name="config")
mcp_app.command("import", help="Import MCP server defs from Claude Code / Codex configs.")(_mcp_import_impl)
mcp_app.command("import-dry", help="List candidate MCP configs without importing.")(_mcp_import_dry_impl)


@app.command("serve", help="Start the long-lived Phantom daemon (sub-50ms perceived start). [Pro]")
def _serve_cmd(
    socket_path: Optional[str] = typer.Option(None, "--socket", help="override socket path"),
    foreground: bool = typer.Option(True, "--foreground/--detach", help="run in foreground (default) or fork"),
) -> None:
    from phantom.licensing import require_pro
    require_pro("serve")
    from phantom.daemon.server import build_default_server, DEFAULT_SOCKET_PATH
    sp = socket_path or DEFAULT_SOCKET_PATH
    typer.echo(f"phantom daemon starting on {sp}")
    server = build_default_server(socket_path=sp)
    if foreground:
        try:
            server.start()
        except KeyboardInterrupt:
            server.stop()
        return
    # detach: double-fork to release the controlling tty
    import os as _os
    if _os.fork() != 0:
        return
    _os.setsid()
    if _os.fork() != 0:
        _os._exit(0)
    server.start()


@app.command("connect", help="Talk to a running Phantom daemon (one-shot op).")
def _connect_cmd(
    op: str = typer.Argument("ping", help="op name (default: ping)"),
    socket_path: Optional[str] = typer.Option(None, "--socket"),
    payload: Optional[str] = typer.Option(None, "--payload", help="JSON payload"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    import json as _json
    from phantom.daemon.client import DaemonClient, DaemonNotRunning
    from phantom.daemon.protocol import DEFAULT_SOCKET_PATH
    sp = socket_path or DEFAULT_SOCKET_PATH
    body = {}
    if payload:
        try:
            body = _json.loads(payload)
        except _json.JSONDecodeError as e:
            typer.echo(f"bad --payload JSON: {e}", err=True)
            raise typer.Exit(2)
    try:
        resp = DaemonClient(socket_path=sp).call(op, **body)
    except DaemonNotRunning as e:
        typer.echo(f"connect: {e}\n(start one with: phantom serve)", err=True)
        raise typer.Exit(1)
    if json_output:
        typer.echo(_json.dumps({"ok": resp.ok, "result": resp.result, "error": resp.error}))
        return
    if resp.ok:
        typer.echo(_json.dumps(resp.result, indent=2) if resp.result else "ok")
    else:
        typer.echo(f"error: {resp.error}", err=True)
        raise typer.Exit(1)


def main() -> None:
    """Console-script entry point. Raises SystemExit on Typer's behalf."""
    app()
