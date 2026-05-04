"""Tests for the swarm runner."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from phantom.swarm import (
    SwarmAgentReport,
    SwarmTask,
    detect_conflicts,
    plan_subtasks,
    run_swarm,
)


# ─── pure-function planner / conflict oracle ─────────────────────────────────


def test_plan_subtasks_default_split():
    tasks = plan_subtasks("refactor auth", 3)
    assert len(tasks) == 3
    assert all(isinstance(t, SwarmTask) for t in tasks)
    assert {t.id for t in tasks} == {"agent-01", "agent-02", "agent-03"}


def test_plan_subtasks_with_custom_planner():
    def planner(goal, n):
        return [f"{goal}#{i}" for i in range(n)]
    tasks = plan_subtasks("g", 2, planner=planner)
    assert [t.description for t in tasks] == ["g#0", "g#1"]


def test_plan_subtasks_planner_must_return_n():
    def bad(goal, n):
        return ["only-one"]
    with pytest.raises(ValueError):
        plan_subtasks("g", 3, planner=bad)


def test_plan_subtasks_rejects_zero():
    with pytest.raises(ValueError):
        plan_subtasks("g", 0)


def test_detect_conflicts_no_overlap():
    r1 = SwarmAgentReport(SwarmTask("a", "x"), "", "", ("a.py",), "", True)
    r2 = SwarmAgentReport(SwarmTask("b", "y"), "", "", ("b.py",), "", True)
    assert detect_conflicts([r1, r2]) == []


def test_detect_conflicts_overlap():
    r1 = SwarmAgentReport(SwarmTask("a", "x"), "", "", ("shared.py", "a.py"), "", True)
    r2 = SwarmAgentReport(SwarmTask("b", "y"), "", "", ("shared.py",), "", True)
    assert detect_conflicts([r1, r2]) == ["shared.py"]


# ─── git-backed integration ──────────────────────────────────────────────────


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if not shutil.which("git"):
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@phantom.dev"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "phantom-test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_run_swarm_returns_clean_when_agent_does_nothing(git_repo: Path):
    result = run_swarm("noop", n=2, repo=git_repo)
    assert len(result.reports) == 2
    assert all(r.ok for r in result.reports)
    assert all(r.files_changed == () for r in result.reports)
    assert result.conflicts == ()


def test_run_swarm_picks_up_file_changes(git_repo: Path):
    def agent(task, wt):
        (wt / f"{task.id}.txt").write_text(task.description)
    result = run_swarm("write per-agent files", n=3, repo=git_repo, agent_fn=agent)
    assert len(result.reports) == 3
    assert all(r.ok for r in result.reports)
    assert all(len(r.files_changed) == 1 for r in result.reports)
    # different agents wrote different files → no conflict
    assert result.conflicts == ()


def test_run_swarm_detects_conflict_on_shared_file(git_repo: Path):
    def agent(task, wt):
        (wt / "shared.txt").write_text(task.description)
    result = run_swarm("clobber shared.txt", n=2, repo=git_repo, agent_fn=agent)
    assert "shared.txt" in result.conflicts


def test_run_swarm_records_agent_exception(git_repo: Path):
    def agent(task, wt):
        raise RuntimeError(f"boom from {task.id}")
    result = run_swarm("fail", n=2, repo=git_repo, agent_fn=agent)
    assert all(not r.ok for r in result.reports)
    assert all("boom" in r.error for r in result.reports)


def test_run_swarm_outside_git_repo(tmp_path: Path):
    result = run_swarm("noop", n=1, repo=tmp_path)
    assert result.reports == ()
    assert result.conflicts and "not a git repo" in result.conflicts[0]
