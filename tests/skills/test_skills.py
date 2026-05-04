"""Tests for :mod:`phantom.skills`."""

from __future__ import annotations

import pytest

from phantom.errors import PhantomError
from phantom.skills import SkillBundle, SkillLoader, builtin_skills_dir


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


class TestSkillBundle:
    def test_load_builtin_git_workflow(self):
        b = SkillBundle.load(builtin_skills_dir() / "git_workflow")
        assert b.name == "git_workflow"
        assert "trunk-based" in b.body
        assert b.tags == ("git", "workflow")
        assert "commit" in b.triggers

    def test_no_skill_md_raises(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(PhantomError, match="no SKILL.md"):
            SkillBundle.load(d)

    def test_missing_frontmatter_raises(self, tmp_path):
        d = tmp_path / "no-front"
        d.mkdir()
        (d / "SKILL.md").write_text("just body, no frontmatter")
        with pytest.raises(PhantomError, match="frontmatter"):
            SkillBundle.load(d)

    def test_missing_required_keys_raises(self, tmp_path):
        d = tmp_path / "minimal"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: x\n---\nbody\n")
        with pytest.raises(PhantomError, match="description"):
            SkillBundle.load(d)

    def test_matches_trigger_case_insensitive(self):
        b = SkillBundle.load(builtin_skills_dir() / "git_workflow")
        assert b.matches("can you Commit this?") is True
        assert b.matches("show me the file") is False


class TestSkillLoader:
    def test_discovers_builtin(self):
        loader = SkillLoader(search_paths=[builtin_skills_dir()])
        bundles = loader.discover()
        names = {b.name for b in bundles}
        assert "git_workflow" in names

    def test_select_for_query(self):
        loader = SkillLoader(search_paths=[builtin_skills_dir()])
        selected = loader.select_for("how do I commit?")
        names = {b.name for b in selected}
        assert "git_workflow" in names

    def test_select_for_no_match(self):
        loader = SkillLoader(search_paths=[builtin_skills_dir()])
        selected = loader.select_for("just chatting about the weather")
        assert all(b.name != "git_workflow" for b in selected)

    def test_skip_dirs_without_skill_md(self, tmp_path):
        (tmp_path / "no-skill").mkdir()
        loader = SkillLoader(search_paths=[tmp_path])
        assert loader.discover() == []

    def test_skip_invalid_skill(self, tmp_path, caplog):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: x\n---\nbody\n")  # no description
        loader = SkillLoader(search_paths=[tmp_path])
        with caplog.at_level("WARNING"):
            bundles = loader.discover()
        assert bundles == []
