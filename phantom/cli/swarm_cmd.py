"""``phantom swarm`` — Typer subcommand."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer

from phantom.swarm import run_swarm

__all__ = ["swarm_cmd"]


def swarm_cmd(
    goal: str = typer.Argument(..., help="the user-level goal"),
    n: int = typer.Option(3, "--agents", "-n", help="how many subagents to spawn"),
    repo: Optional[str] = typer.Option(None, "--repo", help="parent git repo (default: cwd)"),
    keep: bool = typer.Option(False, "--keep", help="keep worktrees even when clean"),
    json_output: bool = typer.Option(False, "--json", help="emit JSON result"),
) -> None:
    """Fan out N subagents into isolated git worktrees and collect diffs."""
    result = run_swarm(
        goal,
        n=n,
        repo=Path(repo) if repo else None,
        keep_worktrees_on_clean=keep,
    )
    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2, default=str))
        return
    typer.echo("")
    typer.echo(f"  swarm goal:       {result.goal}")
    typer.echo(f"  parent repo:      {result.parent_repo}")
    typer.echo(f"  agents ok:        {result.n_ok}/{len(result.reports)}")
    if result.conflicts:
        typer.echo(f"  conflict files:   {len(result.conflicts)}")
        for f in result.conflicts:
            typer.echo(f"    ! {f}")
    for r in result.reports:
        status = "OK " if r.ok else "ERR"
        typer.echo(f"  [{status}] {r.task.id}  {r.duration_s:>6.2f}s  files={len(r.files_changed)}")
        if r.error:
            typer.echo(f"        error: {r.error}")
        if r.worktree_path:
            typer.echo(f"        worktree: {r.worktree_path}")
    typer.echo("")
