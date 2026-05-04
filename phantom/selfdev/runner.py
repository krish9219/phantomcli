"""Self-dev orchestration.

Lifecycle
---------

1. ``git worktree add`` a branch off HEAD into a tmp dir.
2. Call ``editor_fn(plan, worktree)`` — this is where the agent
   actually edits files. In tests it's a deterministic patch.
3. Run the test command (defaults to ``pytest -x -q``) inside the
   worktree.
4. If green: report success, leave worktree alone for the user to
   ``git merge``. We do **not** auto-swap into the parent — that's
   the user's call.
5. If red or editor crashes: tear down worktree, surface diagnostics.

The "swap binary" step the README promises is the user merging the
worktree branch in. Auto-swap is gated behind ``--swap``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

__all__ = ["SelfDevPlan", "SelfDevResult", "run_selfdev"]

log = logging.getLogger("phantom.selfdev")


@dataclass(frozen=True, slots=True)
class SelfDevPlan:
    description: str
    branch: str = ""


@dataclass(frozen=True, slots=True)
class SelfDevResult:
    plan: SelfDevPlan
    worktree_path: str
    branch: str
    files_changed: tuple[str, ...] = field(default_factory=tuple)
    diff: str = ""
    tests_ok: bool = False
    test_stdout: str = ""
    test_stderr: str = ""
    duration_s: float = 0.0
    swapped: bool = False
    error: str = ""


EditorFn = Callable[[SelfDevPlan, Path], None]
"""``editor_fn(plan, worktree)`` — apply the change inside the worktree."""


def _git(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *cmd], cwd=str(cwd), capture_output=True, text=True
    )


def _is_git_repo(p: Path) -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"], p)
    return r.returncode == 0 and r.stdout.strip() == "true"


def run_selfdev(
    description: str,
    *,
    editor_fn: EditorFn,
    repo: Path | str | None = None,
    test_cmd: Sequence[str] = ("pytest", "-x", "-q"),
    swap: bool = False,
    test_timeout_s: float = 600.0,
) -> SelfDevResult:
    """Apply ``editor_fn`` in a worktree, run tests, optionally merge.

    Returns a :class:`SelfDevResult`. Never raises for editor or test
    failures — those live on the result.
    """
    repo_path = Path(repo) if repo else Path.cwd()
    plan = SelfDevPlan(description=description, branch=f"phantom/selfdev-{uuid.uuid4().hex[:8]}")

    if not _is_git_repo(repo_path):
        return SelfDevResult(
            plan=plan, worktree_path="", branch="",
            error=f"{repo_path} is not a git repo",
        )

    import tempfile
    safe = plan.branch.replace("/", "-")
    wt = Path(tempfile.mkdtemp(prefix=f"phantom-selfdev-{safe}-"))
    add = _git(["worktree", "add", "-b", plan.branch, str(wt)], repo_path)
    if add.returncode != 0:
        shutil.rmtree(wt, ignore_errors=True)
        return SelfDevResult(
            plan=plan, worktree_path="", branch=plan.branch,
            error=f"git worktree add failed: {add.stderr.strip()}",
        )

    t0 = time.perf_counter()
    edit_err = ""
    try:
        editor_fn(plan, wt)
    except Exception as e:
        edit_err = f"{type(e).__name__}: {e}"

    files_res = _git(["status", "--porcelain"], wt)
    files = tuple(line[3:].strip() for line in files_res.stdout.splitlines() if line.strip())
    diff_res = _git(["diff", "HEAD"], wt)
    diff = diff_res.stdout

    tests_ok = False
    test_stdout = ""
    test_stderr = ""
    if not edit_err and files:
        try:
            tres = subprocess.run(
                list(test_cmd),
                cwd=str(wt),
                capture_output=True,
                text=True,
                timeout=test_timeout_s,
            )
            tests_ok = tres.returncode == 0
            test_stdout = tres.stdout[-4096:]
            test_stderr = tres.stderr[-4096:]
        except subprocess.TimeoutExpired as e:
            test_stderr = f"test command timed out after {test_timeout_s}s"
            tests_ok = False
    elif not files:
        edit_err = edit_err or "editor made no changes"

    swapped = False
    if tests_ok and swap:
        # Commit the worktree changes so the merge has something to bring in.
        _git(["add", "-A"], wt)
        _git(["-c", "user.email=phantom@selfdev", "-c", "user.name=phantom-selfdev",
              "commit", "-m", f"phantom self-dev: {plan.description}"], wt)
        head_res = _git(["symbolic-ref", "--short", "HEAD"], repo_path)
        target_branch = head_res.stdout.strip() or "main"
        merge = _git(["merge", "--no-edit", "--no-ff", plan.branch], repo_path)
        swapped = merge.returncode == 0
        if not swapped:
            edit_err = (edit_err + " " if edit_err else "") + (
                f"auto-swap into {target_branch} failed: {merge.stderr.strip()}"
            )

    elapsed = time.perf_counter() - t0

    if not tests_ok and not swap:
        # No swap — keep the worktree so the user can debug.
        pass
    if tests_ok and not swap:
        # Keep the worktree for the user to inspect / merge themselves.
        pass

    return SelfDevResult(
        plan=plan,
        worktree_path=str(wt),
        branch=plan.branch,
        files_changed=files,
        diff=diff,
        tests_ok=tests_ok,
        test_stdout=test_stdout,
        test_stderr=test_stderr,
        duration_s=round(elapsed, 3),
        swapped=swapped,
        error=edit_err,
    )
