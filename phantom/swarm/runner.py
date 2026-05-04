"""Swarm orchestration.

We deliberately keep this importable without the full agent loop wired
up — the planner and conflict oracle are pure functions, the runner
takes a callable so tests can stub out the agent. Stage 4's ACP runtime
will plug into the same callable surface.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

__all__ = [
    "SwarmAgentReport",
    "SwarmResult",
    "SwarmTask",
    "detect_conflicts",
    "plan_subtasks",
    "run_swarm",
]

log = logging.getLogger("phantom.swarm")


# ─── records ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwarmTask:
    id: str
    description: str


@dataclass(frozen=True, slots=True)
class SwarmAgentReport:
    task: SwarmTask
    worktree_path: str
    branch: str
    files_changed: tuple[str, ...]
    diff: str
    ok: bool
    error: str = ""
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class SwarmResult:
    goal: str
    reports: tuple[SwarmAgentReport, ...] = field(default_factory=tuple)
    conflicts: tuple[str, ...] = field(default_factory=tuple)
    parent_repo: str = ""

    @property
    def n_ok(self) -> int:
        return sum(1 for r in self.reports if r.ok)


# ─── planner ─────────────────────────────────────────────────────────────────


def plan_subtasks(
    goal: str,
    n: int,
    *,
    planner: Callable[[str, int], list[str]] | None = None,
) -> list[SwarmTask]:
    """Split ``goal`` into ``n`` subtasks.

    If a ``planner`` callable is supplied (e.g. an LLM call), use it.
    Otherwise emit a deterministic naive split — useful for tests, smoke
    runs, and as a fallback when no model is configured.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    if planner is not None:
        descs = planner(goal, n)
        if len(descs) != n:
            raise ValueError(f"planner returned {len(descs)} subtasks, expected {n}")
    else:
        descs = [f"{goal} — slice {i + 1}/{n}" for i in range(n)]
    return [SwarmTask(id=f"agent-{i + 1:02d}", description=d) for i, d in enumerate(descs)]


# ─── git worktree management ─────────────────────────────────────────────────


def _git(cmd: list[str], *, cwd: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *cmd],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _is_git_repo(path: Path) -> bool:
    res = _git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return res.returncode == 0 and res.stdout.strip() == "true"


def _create_worktree(repo: Path, branch: str) -> Path:
    """Create a fresh worktree on ``branch`` (created from HEAD)."""
    safe = branch.replace("/", "-")
    tmp = Path(tempfile.mkdtemp(prefix=f"phantom-swarm-{safe}-"))
    res = _git(["worktree", "add", "-b", branch, str(tmp)], cwd=repo)
    if res.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"git worktree add failed: {res.stderr.strip()}")
    return tmp


def _remove_worktree(repo: Path, path: Path, branch: str, *, keep: bool) -> None:
    if keep:
        return
    _git(["worktree", "remove", "--force", str(path)], cwd=repo)
    _git(["branch", "-D", branch], cwd=repo)
    shutil.rmtree(path, ignore_errors=True)


def _files_and_diff(worktree: Path) -> tuple[tuple[str, ...], str]:
    files_res = _git(["status", "--porcelain"], cwd=worktree)
    files = tuple(
        line[3:].strip()
        for line in files_res.stdout.splitlines()
        if line.strip()
    )
    diff_res = _git(["diff", "HEAD"], cwd=worktree)
    return files, diff_res.stdout


# ─── conflict oracle ─────────────────────────────────────────────────────────


def detect_conflicts(reports: Sequence[SwarmAgentReport]) -> list[str]:
    """Return a list of files edited by 2+ agents.

    A real conflict requires overlapping hunks; we use file-level
    overlap as the cheap, conservative signal — a file touched by two
    agents is reported even if their hunks don't overlap. Callers can
    diff-merge themselves to confirm.
    """
    seen: dict[str, list[str]] = {}
    for r in reports:
        for f in r.files_changed:
            seen.setdefault(f, []).append(r.task.id)
    return sorted(f for f, agents in seen.items() if len(agents) > 1)


# ─── runner ──────────────────────────────────────────────────────────────────


AgentFn = Callable[[SwarmTask, Path], None]
"""Callable signature for an agent run inside a worktree.

Receives the subtask and the worktree path. May raise to signal
failure. Return value is ignored — the runner reads the worktree's
git diff to determine what changed.
"""


def run_swarm(
    goal: str,
    n: int,
    *,
    repo: Path | str | None = None,
    agent_fn: AgentFn | None = None,
    planner: Callable[[str, int], list[str]] | None = None,
    keep_worktrees_on_clean: bool = False,
) -> SwarmResult:
    """Fan out N agents and collect their reports.

    Parameters
    ----------
    goal:
        The high-level user goal.
    n:
        Number of subagents to spawn.
    repo:
        Path to the parent git repo. Defaults to cwd. Must be a git repo;
        if not, returns a SwarmResult with no reports and a clear error
        in the conflicts list.
    agent_fn:
        Function called inside each worktree. Defaults to a no-op (handy
        for testing the orchestration without an LLM).
    planner:
        Optional callable to split the goal. See :func:`plan_subtasks`.
    keep_worktrees_on_clean:
        If True, worktrees are preserved even when the agent made no
        changes. Default False matches Claude Code's worktree isolation
        cleanup behaviour.
    """
    repo_path = Path(repo) if repo else Path.cwd()
    if not _is_git_repo(repo_path):
        return SwarmResult(
            goal=goal,
            reports=(),
            conflicts=(f"{repo_path} is not a git repo",),
            parent_repo=str(repo_path),
        )

    tasks = plan_subtasks(goal, n, planner=planner)
    fn = agent_fn or (lambda task, wt: None)
    reports: list[SwarmAgentReport] = []
    for task in tasks:
        branch = f"phantom/swarm-{task.id}-{uuid.uuid4().hex[:6]}"
        try:
            wt = _create_worktree(repo_path, branch)
        except RuntimeError as e:
            reports.append(SwarmAgentReport(
                task=task, worktree_path="", branch=branch,
                files_changed=(), diff="", ok=False, error=str(e),
            ))
            continue
        t0 = time.perf_counter()
        ok = True
        err = ""
        try:
            fn(task, wt)
        except Exception as e:
            ok = False
            err = f"{type(e).__name__}: {e}"
        files, diff = _files_and_diff(wt)
        elapsed = time.perf_counter() - t0
        clean = (not files) and ok
        _remove_worktree(repo_path, wt, branch, keep=(not clean) or keep_worktrees_on_clean)
        reports.append(SwarmAgentReport(
            task=task,
            worktree_path=str(wt) if (not clean or keep_worktrees_on_clean) else "",
            branch=branch if (not clean or keep_worktrees_on_clean) else "",
            files_changed=files,
            diff=diff,
            ok=ok,
            error=err,
            duration_s=round(elapsed, 3),
        ))
    return SwarmResult(
        goal=goal,
        reports=tuple(reports),
        conflicts=tuple(detect_conflicts(reports)),
        parent_repo=str(repo_path),
    )
