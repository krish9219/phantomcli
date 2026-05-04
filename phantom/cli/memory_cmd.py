"""``phantom memory ...`` subcommands."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer

from phantom.memory.importers import all_importers
from phantom.memory.importers.orchestrator import import_to_memory

__all__ = ["memory_app"]


memory_app = typer.Typer(
    name="memory",
    help="Import and inspect Phantom's memory store.",
    no_args_is_help=True,
)


@memory_app.command("import")
def import_cmd(
    source: str = typer.Argument(..., help="claude-code | codex | opencode"),
    root: Optional[str] = typer.Option(None, "--root", help="override the source's default scan path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="walk transcripts but write nothing"),
    json_output: bool = typer.Option(False, "--json", help="emit JSON summary"),
) -> None:
    """Import another harness's transcripts into Phantom's memory."""
    registry = all_importers()
    if source not in registry:
        typer.echo(f"unknown source: {source!r}. valid: {', '.join(sorted(registry))}", err=True)
        raise typer.Exit(2)
    importer = registry[source](root=Path(root) if root else None)
    summary = import_to_memory(importer, store=None, dry_run=dry_run)
    if json_output:
        typer.echo(json.dumps(asdict(summary), indent=2))
        return
    typer.echo("")
    typer.echo(f"  imported from: {summary.source}")
    typer.echo(f"  sessions:      {summary.sessions}")
    typer.echo(f"  turns:         {summary.turns}")
    typer.echo(f"  written:       {summary.written}")
    typer.echo(f"  skipped:       {summary.skipped}")
    typer.echo("")


@memory_app.command("sources")
def sources_cmd() -> None:
    """List supported import sources."""
    for name, cls in sorted(all_importers().items()):
        inst = cls()
        exists = "yes" if inst.root.exists() else "no "
        typer.echo(f"  [{exists}] {name:<14} {inst.root}")
