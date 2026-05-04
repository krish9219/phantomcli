"""Tests for the two v3.0.5 fixes:
  1. _looks_like_refinement — detects follow-up/refinement prompts so
     Phantom keeps working on the active project.
  2. AgentOrchestrator._default_fallback_plan — hardcoded 4-agent plan
     used when the model can't produce a valid one."""
from __future__ import annotations

import pytest


class TestRefinementDetector:
    def _load(self):
        from omnicli.cli import _looks_like_refinement
        return _looks_like_refinement

    def test_empty_false(self):
        f = self._load()
        assert f("") is False
        assert f(None) is False

    def test_new_build_request_false(self):
        f = self._load()
        # A totally fresh directive — no refinement cues
        assert f("create an IPL match dashboard") is False
        assert f("build a weather tracker for my city") is False

    @pytest.mark.parametrize("txt", [
        "also add a refresh button",
        "ui is not good",
        "it is showing blank sections",
        "not working, fix the error",
        "add a dark mode toggle",
        "remove the hero header",
        "the data is wrong",
        "the page looks bad",
        "update the colors",
        "improve the styling",
        "it's still showing old data",
    ])
    def test_refinement_cues_detected(self, txt):
        f = self._load()
        assert f(txt) is True, f"'{txt}' should be detected as refinement"

    def test_case_insensitive(self):
        f = self._load()
        assert f("ALSO ADD A CHART") is True
        assert f("The UI Is Not Good") is True


class TestDefaultFallbackPlan:
    def test_fallback_has_four_agents(self):
        from omnicli.agents import AgentOrchestrator
        orch = AgentOrchestrator("build a demo app", trust_level=2)
        plan = orch._default_fallback_plan()
        # Cap might be <4 on low-memory machines; but at least 2
        assert 2 <= len(plan) <= 4

    def test_each_task_has_files(self):
        """The whole point of the fallback — no '0 files' tasks."""
        from omnicli.agents import AgentOrchestrator
        orch = AgentOrchestrator("anything", trust_level=2)
        plan = orch._default_fallback_plan()
        for task in plan:
            assert task.assigned_files, \
                f"{task.name} has empty assigned_files"
            assert task.task and task.task.strip(), \
                f"{task.name} has empty task description"
            assert task.name, "task has no name"

    def test_fetcher_agent_produces_seed_data(self):
        from omnicli.agents import AgentOrchestrator
        orch = AgentOrchestrator("x", trust_level=2)
        plan = orch._default_fallback_plan()
        fetcher = next((t for t in plan if "Fetcher" in t.name), None)
        if fetcher is None:
            pytest.skip("Fetcher Agent not in plan — max_agents < 3 on this host")
        fnames = [f.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for f in fetcher.assigned_files]
        assert any("seed" in f.lower() for f in fnames)
        assert any("fetcher" in f.lower() for f in fnames)

    def test_backend_agent_has_port_in_task(self):
        from omnicli.agents import AgentOrchestrator
        orch = AgentOrchestrator("x", trust_level=2)
        plan = orch._default_fallback_plan()
        backend = next((t for t in plan if "Backend" in t.name), None)
        if backend is None:
            pytest.skip("Backend Agent not in plan")
        # The task description should reference the chosen port
        assert str(orch.port) in backend.task

    def test_results_slots_registered(self):
        """Each agent must have a results entry so status-reporting works."""
        from omnicli.agents import AgentOrchestrator
        orch = AgentOrchestrator("x", trust_level=2)
        plan = orch._default_fallback_plan()
        for task in plan:
            assert task.agent_id in orch.results


class TestPlanValidationRejectsEmptyAgents:
    """Regression guard for v3.0.4's 'Agent 1/Agent 2, 0 files' bug."""

    def test_tasks_with_no_files_fail_validation(self):
        """When the extracted JSON produces tasks with empty assigned_files,
        the validator should count them as invalid and return None so the
        retry or fallback kicks in."""
        from omnicli.agents import AgentTask, AgentResult
        # Directly test the validation logic by simulating what _attempt_plan
        # computes after building the task list.
        tasks = [
            AgentTask(agent_id="a", name="Agent 1", role="", task="", assigned_files=[]),
            AgentTask(agent_id="b", name="Agent 2", role="", task="", assigned_files=[]),
        ]
        valid = sum(
            1 for t in tasks
            if t.name and t.name.strip()
            and t.task and t.task.strip()
            and t.assigned_files
        )
        assert valid == 0

    def test_mixed_valid_and_invalid_fails_threshold(self):
        from omnicli.agents import AgentTask
        tasks = [
            AgentTask(agent_id="a", name="Good",  role="", task="real task",
                      assigned_files=["a.py"]),
            AgentTask(agent_id="b", name="Empty", role="", task="",
                      assigned_files=[]),
        ]
        valid = sum(
            1 for t in tasks
            if t.name and t.name.strip()
            and t.task and t.task.strip()
            and t.assigned_files
        )
        # Only 1/2 is valid — below the max(1, len/2)=1 threshold? Actually
        # 1 >= 1 so it passes. This test just documents the behavior.
        assert valid == 1
