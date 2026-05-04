"""Import MCP server definitions from competing harnesses.

Reads ``~/.claude/mcp.json``, ``~/.codex/mcp.json``, and project-local
``.claude/mcp.json`` / ``.codex/mcp.json`` (cwd-relative). Merges them
into Phantom's MCP config at ``~/.phantom/mcp.json``.

Conflict policy
---------------

* If a server name exists in both Phantom's config and an imported
  source, the existing entry wins (preserve user intent).
* If the same name appears in two sources, the first source wins
  (claude-code → codex → project-local).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "MCPImportSummary",
    "discover_sources",
    "import_mcp_configs",
    "phantom_mcp_path",
]


def phantom_mcp_path() -> Path:
    base = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom"))
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / "mcp.json"


def discover_sources(cwd: Path | None = None) -> list[tuple[str, Path]]:
    """Return ``[(label, path), ...]`` for every config we'd consider.

    Order matters — earlier wins on ties. The list is deduped by resolved
    path so a project run from $HOME doesn't see the same file twice.
    """
    home = Path(os.path.expanduser("~"))
    cwd = cwd or Path.cwd()
    candidates = [
        ("claude-code (user)", home / ".claude" / "mcp.json"),
        ("codex (user)",       home / ".codex" / "mcp.json"),
        ("claude-code (project)", cwd / ".claude" / "mcp.json"),
        ("codex (project)",       cwd / ".codex" / "mcp.json"),
    ]
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for label, p in candidates:
        if not p.is_file():
            continue
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append((label, p))
    return out


@dataclass(frozen=True, slots=True)
class MCPImportSummary:
    sources_seen: int
    servers_added: int
    servers_skipped_existing: int
    servers_skipped_invalid: int
    target: str
    added_names: tuple[str, ...]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize_servers(blob: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pull the {name: {command, args, env}} map out of a harness config.

    Both Claude Code and Codex use ``mcpServers`` at the top level.
    """
    raw = blob.get("mcpServers")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, body in raw.items():
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(body, dict):
            continue
        # require at minimum a command string
        if not isinstance(body.get("command"), str):
            continue
        normalized = {
            "command": body["command"],
            "args": list(body.get("args") or []),
            "env": dict(body.get("env") or {}),
        }
        out[name] = normalized
    return out


def import_mcp_configs(
    *,
    cwd: Path | None = None,
    target: Path | None = None,
    dry_run: bool = False,
) -> MCPImportSummary:
    """Walk discovered sources and merge into Phantom's MCP config."""
    target_path = target or phantom_mcp_path()
    existing = _read_json(target_path) if target_path.exists() else {}
    if "mcpServers" not in existing or not isinstance(existing.get("mcpServers"), dict):
        existing["mcpServers"] = {}
    target_servers: dict[str, dict[str, Any]] = existing["mcpServers"]

    sources = discover_sources(cwd=cwd)
    added: list[str] = []
    skipped_existing = 0
    skipped_invalid = 0
    seen_in_sources: set[str] = set()

    for _label, path in sources:
        servers = _normalize_servers(_read_json(path))
        if not servers:
            skipped_invalid += 1
            continue
        for name, body in servers.items():
            if name in target_servers:
                skipped_existing += 1
                continue
            if name in seen_in_sources:
                # earlier source already won
                continue
            seen_in_sources.add(name)
            target_servers[name] = body
            added.append(name)

    if not dry_run and added:
        target_path.write_text(json.dumps(existing, indent=2, sort_keys=True))
        try:
            os.chmod(target_path, 0o600)
        except OSError:
            pass

    return MCPImportSummary(
        sources_seen=len(sources),
        servers_added=len(added),
        servers_skipped_existing=skipped_existing,
        servers_skipped_invalid=skipped_invalid,
        target=str(target_path),
        added_names=tuple(added),
    )
