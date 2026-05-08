"""``phantom self-dev`` — Typer subcommand.

Today this is wired with a stub editor_fn (no real LLM hookup yet).
The CLI surface is stable so the engine wiring can land later without
breaking tests or operator scripts.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer

from phantom.selfdev import run_selfdev

__all__ = ["selfdev_cmd"]


def _stub_editor(plan, worktree: Path) -> None:
    """Default editor: no-op. Real editor_fn arrives with engine wiring."""
    return None


def selfdev_cmd(
    description: str = typer.Argument(..., help="describe the change you want"),
    repo: Optional[str] = typer.Option(None, "--repo", help="parent git repo (default: cwd)"),
    swap: bool = typer.Option(False, "--swap", help="auto-merge if tests are green"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Apply a self-dev change in an isolated worktree, run tests, report."""
    from phantom.licensing import require_pro
    require_pro("self-dev")
    result = run_selfdev(
        description,
        editor_fn=_stub_editor,
        repo=Path(repo) if repo else None,
        swap=swap,
    )
    if json_output:
        body = asdict(result)
        # diff and stdout can be huge; truncate for readability
        body["diff"] = body["diff"][:2000]
        body["test_stdout"] = body["test_stdout"][:2000]
        body["test_stderr"] = body["test_stderr"][:2000]
        typer.echo(json.dumps(body, indent=2, default=str))
        return
    typer.echo("")
    typer.echo(f"  goal:        {result.plan.description}")
    typer.echo(f"  branch:      {result.branch}")
    typer.echo(f"  worktree:    {result.worktree_path}")
    typer.echo(f"  files:       {len(result.files_changed)}")
    typer.echo(f"  duration:    {result.duration_s:.2f}s")
    typer.echo(f"  tests ok:    {result.tests_ok}")
    if result.swapped:
        typer.echo(f"  swapped:     yes")
    if result.error:
        typer.echo(f"  error:       {result.error}")
    typer.echo("")
