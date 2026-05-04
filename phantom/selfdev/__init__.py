"""Sandboxed self-development.

The agent edits Phantom's own source inside a git worktree, runs the
full test suite there, and only swaps the binary if green. jcode lets
the agent edit live; we don't — the worktree + green-tests gate is the
moat.

Public surface
--------------

* :class:`SelfDevPlan` / :class:`SelfDevResult` — immutable records.
* :func:`run_selfdev` — orchestration entrypoint. Pure-ish: takes an
  ``editor_fn`` callable so tests can supply a deterministic patch.
"""

from __future__ import annotations

from phantom.selfdev.runner import (
    SelfDevPlan,
    SelfDevResult,
    run_selfdev,
)

__all__ = ["SelfDevPlan", "SelfDevResult", "run_selfdev"]
