"""Tests for git worktree isolation. These use REAL git subprocesses —
they're skipped if git isn't on PATH."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from omnicli import worktree as wt


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not installed on PATH",
)


@pytest.fixture
def git_repo(tmp_path):
    """Build a throwaway git repo with one commit so worktrees can branch off."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example"}
    def _run(*args, cwd=repo):
        subprocess.run(args, cwd=cwd, env=env, check=True,
                       capture_output=True, text=True)
    _run("git", "init", "-q", "-b", "main")
    # Need a commit before worktree add works
    (repo / "README.md").write_text("initial")
    _run("git", "add", "README.md")
    _run("git", "commit", "-q", "-m", "initial")
    return repo


class TestIsGitRepo:
    def test_detects_git_repo(self, git_repo):
        assert wt.is_git_repo(str(git_repo)) is True

    def test_non_repo_returns_false(self, tmp_path):
        d = tmp_path / "not-a-repo"
        d.mkdir()
        assert wt.is_git_repo(str(d)) is False


class TestCreateSkipsNonRepo:
    def test_non_repo_falls_back_gracefully(self, tmp_path):
        """When the parent isn't a git repo, create() returns a non-isolated
        Worktree pointing at the original path — no exception."""
        d = tmp_path / "plain"
        d.mkdir()
        w = wt.create(str(d), agent_label="demo")
        assert w.isolated is False
        assert w.path == os.path.abspath(str(d))
        assert w.branch == ""


class TestCreateInRepo:
    def test_new_worktree_created(self, git_repo):
        w = wt.create(str(git_repo), agent_label="explore")
        try:
            assert w.isolated is True
            assert os.path.isdir(w.path)
            assert os.path.isfile(os.path.join(w.path, "README.md"))
            assert w.branch.startswith("phantom/explore-")
        finally:
            w.cleanup(preserve_if_changed=False)

    def test_branch_name_sanitized(self, git_repo):
        w = wt.create(str(git_repo), agent_label="bad label/with*chars")
        try:
            # Spaces and / and * must be replaced with -
            assert " " not in w.branch
            assert "*" not in w.branch
            # But the branch name itself contains a / (phantom/...) which is fine for git.
            assert w.branch.count("/") == 1
        finally:
            w.cleanup(preserve_if_changed=False)

    def test_unique_ids_for_concurrent_worktrees(self, git_repo):
        w1 = wt.create(str(git_repo), agent_label="x")
        w2 = wt.create(str(git_repo), agent_label="x")
        try:
            assert w1.path != w2.path
            assert w1.branch != w2.branch
        finally:
            w1.cleanup(preserve_if_changed=False)
            w2.cleanup(preserve_if_changed=False)


class TestCleanupNoChanges:
    def test_clean_worktree_removed(self, git_repo):
        w = wt.create(str(git_repo))
        path = w.path
        result = w.cleanup(preserve_if_changed=False)
        assert result["removed"] is True
        assert result["changed_files"] == 0
        assert not os.path.exists(path)

    def test_auto_cleanup_when_no_changes_and_preserve_on(self, git_repo):
        """Even with preserve_if_changed=True, a clean worktree (0 changes)
        should be removed — there's nothing to preserve."""
        w = wt.create(str(git_repo))
        path = w.path
        result = w.cleanup(preserve_if_changed=True)
        assert result["removed"] is True
        assert not os.path.exists(path)


class TestCleanupWithChanges:
    def test_preserved_when_files_edited(self, git_repo):
        w = wt.create(str(git_repo))
        # Make a change inside the worktree
        (open(os.path.join(w.path, "new.txt"), "w")).write("hello")
        result = w.cleanup(preserve_if_changed=True)
        assert result["removed"] is False
        assert result["changed_files"] >= 1
        assert "changed" in (result["preserved_because"] or "")
        # Worktree dir still exists
        assert os.path.isdir(w.path)
        # Clean up manually for the test
        wt._remove_worktree(str(git_repo), w.path)
        wt._delete_branch(str(git_repo), w.branch)

    def test_force_remove_even_with_changes(self, git_repo):
        w = wt.create(str(git_repo))
        (open(os.path.join(w.path, "new.txt"), "w")).write("x")
        result = w.cleanup(preserve_if_changed=False)
        assert result["removed"] is True
        assert not os.path.exists(w.path)


class TestMainRepoUntouched:
    def test_main_tree_unchanged_while_worktree_edits(self, git_repo):
        w = wt.create(str(git_repo))
        try:
            # Write in the worktree
            (open(os.path.join(w.path, "new.txt"), "w")).write("hello")
            # Verify main tree has NO new.txt
            assert not os.path.exists(os.path.join(str(git_repo), "new.txt"))
        finally:
            w.cleanup(preserve_if_changed=False)


class TestBranchLifecycle:
    def test_branch_deleted_on_cleanup(self, git_repo):
        w = wt.create(str(git_repo))
        branch = w.branch
        w.cleanup(preserve_if_changed=False)
        # Verify branch is gone
        r = subprocess.run(
            ["git", "-C", str(git_repo), "branch", "--list", branch],
            capture_output=True, text=True,
        )
        assert r.stdout.strip() == ""
