"""
Git worktree isolation for subagents — mirrors Claude Code's
`isolation: "worktree"` option on the Agent tool.

When a subagent runs with worktree isolation:
  1. We create `git worktree add <tmp_path> -b phantom/agent-<id>` from
     the main repo's HEAD, giving the subagent a clean checkout on a
     throwaway branch.
  2. The subagent works in that path. Its edits don't touch the main
     working tree.
  3. On clean exit:
       - If NO files changed: `git worktree remove --force` and delete
         the branch. The path disappears, no clutter.
       - If files changed: we leave the worktree + branch in place and
         return the path so the parent agent (or user) can inspect the
         diff / cherry-pick / merge.
  4. On error exit we also preserve the worktree so crash diagnostics
     aren't lost.

Compatible with any git repo. If the cwd is NOT a git repo, worktree
isolation is silently skipped and the caller is handed back the original
cwd (caller should check `isolated` on the returned dataclass).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("omnicli.worktree")


@dataclass
class Worktree:
    path:       str
    branch:     str
    parent_dir: str
    isolated:   bool = True

    def cleanup(self, preserve_if_changed: bool = True) -> dict:
        """Remove the worktree + branch if no changes were made. Returns
        {'removed': bool, 'preserved_because': str|None, 'changed_files': int}.

        `preserve_if_changed=True` (default) mirrors Claude Code: when the
        subagent actually wrote something, we keep the worktree so the
        user can inspect it. Pass `False` to force-remove either way."""
        if not self.isolated:
            return {"removed": False, "preserved_because": "not isolated", "changed_files": 0}

        changed = _count_changes(self.path)
        if preserve_if_changed and changed > 0:
            log.info("worktree %s preserved — %d changes present", self.path, changed)
            return {
                "removed": False,
                "preserved_because": f"{changed} files changed",
                "changed_files": changed,
                "path": self.path,
                "branch": self.branch,
            }
        _remove_worktree(self.parent_dir, self.path)
        _delete_branch(self.parent_dir, self.branch)
        return {"removed": True, "preserved_because": None, "changed_files": changed}


# ─── Public API ──────────────────────────────────────────────────────────────


def is_git_repo(path: str) -> bool:
    """True iff `path` is inside a git working tree."""
    try:
        r = subprocess.run(
            ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def create(
    parent_dir:   str,
    agent_label:  str = "agent",
    base_ref:     str = "HEAD",
) -> Worktree:
    """Create a new worktree. Returns a Worktree dataclass whose `isolated`
    is False if the parent isn't a git repo (caller should fall back to
    working directly in parent_dir)."""
    parent_dir = os.path.abspath(parent_dir)
    if not is_git_repo(parent_dir):
        return Worktree(path=parent_dir, branch="", parent_dir=parent_dir, isolated=False)

    label_clean = _sanitize(agent_label)
    wid = uuid.uuid4().hex[:8]
    branch = f"phantom/{label_clean}-{wid}"
    wt_path = os.path.join(tempfile.gettempdir(), f"phantom-wt-{label_clean}-{wid}")

    cmd = ["git", "-C", parent_dir, "worktree", "add", "-b", branch, wt_path, base_ref]
    log.debug("creating worktree: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {r.stderr.strip()}")
    return Worktree(path=wt_path, branch=branch, parent_dir=parent_dir, isolated=True)


# ─── Internals ───────────────────────────────────────────────────────────────


def _sanitize(label: str) -> str:
    """Make a label git-branch-safe. Keeps [A-Za-z0-9-_], replaces the rest."""
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
    out = out.strip("-") or "agent"
    return out[:48]


def _count_changes(path: str) -> int:
    """Count files that differ from HEAD in `path`, including untracked."""
    try:
        r = subprocess.run(
            ["git", "-C", path, "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return 0
        return sum(1 for line in r.stdout.splitlines() if line.strip())
    except (OSError, subprocess.TimeoutExpired):
        return 0


def _remove_worktree(parent_dir: str, wt_path: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", parent_dir, "worktree", "remove", "--force", wt_path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            # Fallback: wipe the directory if git can't (worktree list stale, etc.)
            shutil.rmtree(wt_path, ignore_errors=True)
            log.warning("git worktree remove failed (%s) — did rmtree fallback",
                        r.stderr.strip())
            return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        shutil.rmtree(wt_path, ignore_errors=True)
        return False


def _delete_branch(parent_dir: str, branch: str) -> bool:
    if not branch:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", parent_dir, "branch", "-D", branch],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


__all__ = ["Worktree", "create", "is_git_repo"]
