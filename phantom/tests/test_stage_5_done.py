"""Stage 5 smoke test."""

from __future__ import annotations

import pytest

from phantom.memory import MemoryStore
from phantom.skills import SkillLoader, builtin_skills_dir


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


@pytest.mark.stage5
def test_skill_loader_finds_git_workflow():
    loader = SkillLoader(search_paths=[builtin_skills_dir()])
    names = {b.name for b in loader.discover()}
    assert "git_workflow" in names


@pytest.mark.stage5
def test_memory_hybrid_retrieval(tmp_path):
    s = MemoryStore.open(tmp_path / "m.db")
    try:
        s.add(user="u", project="p", session="x", kind="n",
              text="Phantom uses bubblewrap as sandbox tier 1.")
        s.add(user="u", project="p", session="x", kind="n",
              text="The cake is a lie.")
        out = s.search(user="u", project="p", query="bubblewrap sandbox")
        assert len(out) >= 1
        assert "bubblewrap" in out[0].text
    finally:
        s.close()


@pytest.mark.stage5
def test_phantom_stage_advanced_to_5_or_higher():
    import phantom
    assert phantom.feature_flags()["stage"] >= 5
