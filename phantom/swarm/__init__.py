"""Phantom swarm — fan out N agents into isolated git worktrees.

The swarm is the user-facing wrapper around the existing
``omnicli.subagents`` registry and ``omnicli.worktree`` helper. It adds:

* A planner step: take a single high-level goal and split it into N
  subtasks (defaults to a deterministic split when no LLM is wired).
* Per-agent isolated worktrees so file edits never collide.
* Diff-based conflict resolution: collect each worktree's diff against
  the parent branch, group by file, and surface true conflicts (overlapping
  hunks) for human or LLM resolution.

Public surface
--------------

* :class:`SwarmTask` / :class:`SwarmResult` — the immutable records.
* :func:`run_swarm` — orchestration entrypoint.
* :func:`detect_conflicts` — diff merge oracle (pure function).
"""

from __future__ import annotations

from phantom.swarm.runner import (
    SwarmAgentReport,
    SwarmResult,
    SwarmTask,
    detect_conflicts,
    plan_subtasks,
    run_swarm,
)

__all__ = [
    "SwarmAgentReport",
    "SwarmResult",
    "SwarmTask",
    "detect_conflicts",
    "plan_subtasks",
    "run_swarm",
]
