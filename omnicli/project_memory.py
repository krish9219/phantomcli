"""
Per-project memory — stops Phantom from creating a fresh project_xxxxxxxx
directory every time the user rephrases the same request.

On successful multi-agent build we write `phantom_summary.md` into the
project directory containing:
  - original directive (what the user typed)
  - refined directive (what improve_prompt produced)
  - files written (path + purpose + size)
  - agents that ran + their status
  - acceptance checks declared in the spec
  - run history (every time Phantom relaunches the app, append a line)

Before starting a new multi-agent build, `find_related_projects()` scans
`~/PhantomProjects/` for existing `phantom_summary.md` files and scores
each against the new directive via simple keyword overlap. The top match
above a threshold is offered to the user: "extend or new?".
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("omnicli.project_memory")

SUMMARY_FILENAME = "phantom_summary.md"

# Words we ignore when computing relatedness (English stopwords + directive
# boilerplate that shows up in every Phantom prompt).
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or",
    "with", "that", "is", "are", "was", "were", "be", "been", "being",
    "it", "its", "this", "these", "those", "me", "my", "i", "you", "your",
    "we", "our", "us", "they", "their", "them", "he", "she", "his", "her",
    "create", "build", "make", "generate", "write", "develop", "design",
    "run", "app", "application", "project", "website", "site", "webapp",
    "dashboard", "tool", "some", "any", "all", "every", "each",
    "can", "could", "should", "would", "will", "do", "does", "did",
    "please", "help", "very", "just", "also", "as", "so", "but", "if",
    "then", "than", "use", "using", "want", "need", "have", "has", "had",
    "one", "two", "three", "new", "old", "more", "most", "other", "than",
    "about", "after", "before", "because", "during", "such", "like",
    "what", "when", "where", "which", "who", "how", "why",
})


# ─── Data model ──────────────────────────────────────────────────────────────


@dataclass
class ProjectSummary:
    project_dir:    str
    session_id:     str
    created_at:     str           = ""
    last_updated:   str           = ""
    directive:      str           = ""
    refined:        str           = ""
    files:          list[dict]    = field(default_factory=list)
    agents:         list[dict]    = field(default_factory=list)
    runs:           list[dict]    = field(default_factory=list)
    relatedness:    float         = 0.0   # filled in by match scorer


# ─── Writing ─────────────────────────────────────────────────────────────────


def write_summary(
    project_dir:   str,
    directive:     str,
    refined:       str,
    files:         list[dict] | None = None,
    agents:        list[dict] | None = None,
    extra_runs:    list[dict] | None = None,
) -> str:
    """Create or refresh the per-project phantom_summary.md. Preserves run
    history from any previous summary in the same dir. Returns the path."""
    path = os.path.join(project_dir, SUMMARY_FILENAME)
    now  = time.strftime("%Y-%m-%dT%H:%M:%S")

    previous_runs: list[dict] = []
    previous_created = now
    try:
        if os.path.isfile(path):
            parsed = _parse_existing(path)
            previous_runs = parsed.runs
            previous_created = parsed.created_at or now
    except Exception as e:
        log.debug("could not parse existing summary: %s", e)

    runs = list(previous_runs)
    if extra_runs:
        runs.extend(extra_runs)

    session_id = os.path.basename(project_dir).replace("project_", "")

    lines: list[str] = [
        "# Phantom Project Summary",
        "",
        "**This file is auto-generated. Phantom reads it on future builds to "
        "decide whether to extend this project or create a new one.**",
        "",
        f"- `project_dir:` `{project_dir}`",
        f"- `session_id:`  `{session_id}`",
        f"- `created_at:`  `{previous_created}`",
        f"- `last_updated:` `{now}`",
        "",
        "## Original directive",
        "",
        "```text",
        directive.strip() or "(not recorded)",
        "```",
        "",
        "## Refined directive",
        "",
        "```text",
        (refined or "(same as original)").strip(),
        "```",
        "",
    ]

    if files:
        lines.append("## Files written")
        lines.append("")
        for f in files:
            p = f.get("path", "?")
            sz = f.get("size", 0)
            purpose = f.get("purpose", "")
            lines.append(f"- `{p}` ({sz:,} bytes){' — ' + purpose if purpose else ''}")
        lines.append("")

    if agents:
        lines.append("## Agents")
        lines.append("")
        for a in agents:
            name = a.get("name", "?")
            role = a.get("role", "")
            status = a.get("status", "")
            elapsed = a.get("elapsed_s", "")
            lines.append(f"- **{name}** ({role}) — {status} · {elapsed}s")
        lines.append("")

    if runs:
        lines.append("## Run history (append-only)")
        lines.append("")
        for r in runs[-20:]:
            ts = r.get("ts", "?")
            action = r.get("action", "?")
            note = r.get("note", "")
            lines.append(f"- `{ts}` · {action}{' · ' + note if note else ''}")
        lines.append("")

    lines.append("---")
    lines.append("_Edit this file manually to record long-form notes; "
                 "Phantom preserves the run-history list on rewrites._")
    lines.append("")

    try:
        os.makedirs(project_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        log.warning("could not write summary: %s", e)
    return path


def append_run(project_dir: str, action: str, note: str = "") -> None:
    """Append one entry to the run history of an existing summary."""
    path = os.path.join(project_dir, SUMMARY_FILENAME)
    try:
        if os.path.isfile(path):
            parsed = _parse_existing(path)
            parsed.runs.append({
                "ts":     time.strftime("%Y-%m-%dT%H:%M:%S"),
                "action": action,
                "note":   note,
            })
            write_summary(
                project_dir=project_dir,
                directive=parsed.directive,
                refined=parsed.refined,
                files=parsed.files,
                agents=parsed.agents,
                extra_runs=parsed.runs,
            )
    except Exception as e:
        log.debug("append_run failed: %s", e)


# ─── Reading / scanning ──────────────────────────────────────────────────────


def _parse_existing(path: str) -> ProjectSummary:
    """Parse a phantom_summary.md back into a ProjectSummary. Permissive —
    missing sections yield empty fields."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return ProjectSummary(project_dir=os.path.dirname(path), session_id="")

    project_dir = os.path.dirname(path)
    session_id  = os.path.basename(project_dir).replace("project_", "")

    def _kv(key: str) -> str:
        m = re.search(rf"- `{re.escape(key)}`\s+`([^`]*)`", raw)
        return m.group(1) if m else ""

    def _block(heading: str) -> str:
        m = re.search(
            rf"## {re.escape(heading)}\s*\n\s*```text\s*\n(.*?)```",
            raw, re.DOTALL,
        )
        return (m.group(1) or "").strip() if m else ""

    # Run history: each "- `TS` · ACTION · NOTE" line
    runs: list[dict] = []
    run_block = re.search(r"## Run history.*?\n(.*?)(?:\n##|\n---)", raw, re.DOTALL)
    if run_block:
        for line in run_block.group(1).splitlines():
            m = re.match(r"- `([^`]+)`\s*·\s*([^·]+?)(?:\s*·\s*(.*))?$", line.strip())
            if m:
                runs.append({"ts": m.group(1), "action": m.group(2).strip(),
                             "note": (m.group(3) or "").strip()})

    return ProjectSummary(
        project_dir=project_dir,
        session_id=session_id,
        created_at=_kv("created_at:"),
        last_updated=_kv("last_updated:"),
        directive=_block("Original directive"),
        refined=_block("Refined directive"),
        files=[],     # not parsed back for scoring purposes
        agents=[],
        runs=runs,
    )


def _work_dir() -> str:
    try:
        from omnicli.memory import get_config
        d = (get_config("work_dir", "") or "").strip()
        if d: return d
    except Exception:
        pass
    return os.path.expanduser(os.path.join("~", "PhantomProjects"))


def _tokenize(text: str) -> set[str]:
    """Lower-case, strip punctuation, drop stopwords + short tokens."""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) >= 3}


def _score(new_tokens: set[str], old_tokens: set[str]) -> float:
    """Jaccard similarity, 0.0–1.0."""
    if not new_tokens or not old_tokens:
        return 0.0
    inter = new_tokens & old_tokens
    union = new_tokens | old_tokens
    return len(inter) / max(1, len(union))


def find_related_projects(
    directive:   str,
    work_dir:    Optional[str] = None,
    min_score:   float = 0.15,
    top_k:       int   = 3,
) -> list[ProjectSummary]:
    """Return the top-K existing projects whose summary overlaps with the
    new directive. Sorted by relatedness descending."""
    base = work_dir or _work_dir()
    if not os.path.isdir(base):
        return []
    new_tokens = _tokenize(directive)

    rows: list[ProjectSummary] = []
    try:
        for entry in os.listdir(base):
            if not entry.startswith("project_"):
                continue
            summary_path = os.path.join(base, entry, SUMMARY_FILENAME)
            if not os.path.isfile(summary_path):
                continue
            try:
                row = _parse_existing(summary_path)
            except Exception:
                continue
            old_tokens = _tokenize(row.directive + " " + row.refined)
            row.relatedness = _score(new_tokens, old_tokens)
            if row.relatedness >= min_score:
                rows.append(row)
    except OSError:
        return []

    rows.sort(key=lambda r: (r.relatedness, r.last_updated), reverse=True)
    return rows[:top_k]


def format_related_prompt(rows: Iterable[ProjectSummary]) -> str:
    """Format a list of related projects for interactive prompt display."""
    lines = []
    for i, r in enumerate(rows, start=1):
        first_line = (r.directive.splitlines() or [""])[0][:80]
        lines.append(
            f"  [{i}] {os.path.basename(r.project_dir)} "
            f"({int(r.relatedness * 100)}% match, updated {r.last_updated})\n"
            f"      {first_line}"
        )
    return "\n".join(lines)


__all__ = [
    "write_summary", "append_run", "find_related_projects",
    "format_related_prompt", "ProjectSummary", "SUMMARY_FILENAME",
]
