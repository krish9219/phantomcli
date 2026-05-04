"""Tests for sandboxed self-dev mode."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from phantom.selfdev import SelfDevResult, run_selfdev


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if not shutil.which("git"):
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@p.dev"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_selfdev_outside_git_repo_returns_error(tmp_path: Path):
    r = run_selfdev("noop", editor_fn=lambda p, wt: None, repo=tmp_path)
    assert isinstance(r, SelfDevResult)
    assert "not a git repo" in r.error
    assert not r.tests_ok


def test_selfdev_no_change_reports_no_files(git_repo: Path):
    r = run_selfdev(
        "noop", editor_fn=lambda p, wt: None,
        repo=git_repo, test_cmd=("true",),
    )
    assert r.files_changed == ()
    assert "made no changes" in r.error
    assert not r.tests_ok


def test_selfdev_with_passing_tests(git_repo: Path):
    def editor(plan, wt: Path):
        (wt / "new.txt").write_text("data")
    r = run_selfdev(
        "add file", editor_fn=editor, repo=git_repo, test_cmd=("true",),
    )
    assert r.files_changed == ("new.txt",)
    assert r.tests_ok is True
    assert r.error == ""
    # worktree preserved for user to merge
    assert Path(r.worktree_path).exists()
    assert (Path(r.worktree_path) / "new.txt").read_text() == "data"


def test_selfdev_with_failing_tests(git_repo: Path):
    def editor(plan, wt: Path):
        (wt / "new.txt").write_text("data")
    r = run_selfdev(
        "add file but tests fail", editor_fn=editor,
        repo=git_repo, test_cmd=("false",),
    )
    assert r.files_changed == ("new.txt",)
    assert r.tests_ok is False
    # diagnostic worktree preserved
    assert Path(r.worktree_path).exists()


def test_selfdev_editor_exception_captured(git_repo: Path):
    def editor(plan, wt: Path):
        raise RuntimeError("editor blew up")
    r = run_selfdev("crash", editor_fn=editor, repo=git_repo, test_cmd=("true",))
    assert "editor blew up" in r.error
    assert not r.tests_ok


def test_selfdev_swap_merges_when_green(git_repo: Path):
    def editor(plan, wt: Path):
        (wt / "merged.txt").write_text("ok")
    r = run_selfdev(
        "merge me", editor_fn=editor, repo=git_repo,
        test_cmd=("true",), swap=True,
    )
    assert r.tests_ok is True
    assert r.swapped is True
    # parent repo should now have the file
    assert (git_repo / "merged.txt").read_text() == "ok"
