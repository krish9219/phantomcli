"""``phantom mcp import`` — pull MCP server defs from Claude Code / Codex."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer

from phantom.mcp.import_config import (
    discover_sources,
    import_mcp_configs,
    phantom_mcp_path,
)

__all__ = ["mcp_import", "mcp_import_dry"]


def mcp_import(
    target: Optional[str] = typer.Option(None, "--target", help="override target mcp.json path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show what would be added without writing"),
    json_output: bool = typer.Option(False, "--json", help="JSON summary"),
) -> None:
    """Import MCP server definitions from Claude Code and Codex configs."""
    target_path = Path(target) if target else None
    summary = import_mcp_configs(target=target_path, dry_run=dry_run)
    if json_output:
        typer.echo(json.dumps(asdict(summary), indent=2))
        return
    typer.echo("")
    typer.echo(f"  sources scanned:  {summary.sources_seen}")
    typer.echo(f"  servers added:    {summary.servers_added}")
    typer.echo(f"  already present:  {summary.servers_skipped_existing}")
    typer.echo(f"  invalid skipped:  {summary.servers_skipped_invalid}")
    typer.echo(f"  target:           {summary.target}")
    if summary.added_names:
        typer.echo("")
        typer.echo("  added:")
        for n in summary.added_names:
            typer.echo(f"    + {n}")
    typer.echo("")


def mcp_import_dry() -> None:
    """List candidate MCP configs without importing."""
    sources = discover_sources()
    if not sources:
        typer.echo("no Claude Code or Codex MCP configs found")
        return
    for label, path in sources:
        typer.echo(f"  {label:<26} {path}")
    typer.echo(f"\n  → would write to: {phantom_mcp_path()}")
