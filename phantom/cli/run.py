"""``phantom run -- <cmd>`` — execute a command inside the sandbox.

Bypasses the agent loop. Useful for:

* Smoke-testing the sandbox after install.
* CI scripts that want sandbox isolation without booting the full
  agent.
* Debugging "why doesn't my command work in Phantom?" — run it directly
  and see the result.

Examples
--------

>>> # $ phantom run -- echo hello
>>> # hello
>>> # $ phantom run --workdir /tmp/scratch -- pwd
>>> # /tmp/scratch
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

import typer

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
from phantom.sandbox.policy import ResourceLimits

__all__ = ["run"]


def run(
    args: List[str] = typer.Argument(
        ..., help="Command to run, after `--`. Example: phantom run -- echo hi"
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
    """Run *args* inside the sandbox and stream the result."""
    if not args:
        typer.echo("phantom run: nothing to execute", err=True)
        raise typer.Exit(2)

    workdir_resolved = workdir or os.getcwd()
    # If the operator asked for a workdir we don't have, create it; this
    # matches `phantom run --workdir /tmp/new -- pwd` ergonomics.
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
    standard ``'\\''`` escape. This is the only safe way to compose a
    sh-c command from arbitrary input.
    """
    return "'" + arg.replace("'", "'\\''") + "'"
