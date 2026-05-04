"""Tests for :mod:`phantom.acp.runtime`."""

from __future__ import annotations

import pytest

from phantom.acp import (
    AgentEvent,
    AgentResult,
    AgentRuntime,
    AgentSpec,
    AgentStatus,
)
from phantom.errors import PhantomError


def _ok_body(spec: AgentSpec, emit) -> dict:
    return {"id": spec.agent_id, "in": dict(spec.inputs)}


def _fail_body(spec: AgentSpec, emit) -> dict:
    raise RuntimeError("planned failure")


class TestSpawnAndRun:
    def test_runs_a_single_agent(self):
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="solo", body=_ok_body, inputs={"x": 1}))
        results = rt.run_all()
        assert results["solo"].status == AgentStatus.COMPLETED
        assert results["solo"].output == {"id": "solo", "in": {"x": 1}}

    def test_duplicate_id_rejected(self):
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="x", body=_ok_body))
        with pytest.raises(PhantomError, match="duplicate"):
            rt.spawn(AgentSpec(agent_id="x", body=_ok_body))

    def test_failure_isolated(self):
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="ok", body=_ok_body))
        rt.spawn(AgentSpec(agent_id="bad", body=_fail_body))
        results = rt.run_all()
        assert results["ok"].status == AgentStatus.COMPLETED
        assert results["bad"].status == AgentStatus.FAILED
        assert "RuntimeError" in results["bad"].error
        assert "planned failure" in results["bad"].error

    def test_body_must_return_dict(self):
        def bad(spec, emit): return "not a dict"  # type: ignore[return-value]
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="x", body=bad))
        results = rt.run_all()
        assert results["x"].status == AgentStatus.FAILED


class TestDependencyWaves:
    def test_runs_in_topological_order(self):
        seen: list[str] = []

        def body(spec, emit):
            seen.append(spec.agent_id)
            return {}

        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="a", body=body))
        rt.spawn(AgentSpec(agent_id="b", body=body, depends_on=("a",)))
        rt.spawn(AgentSpec(agent_id="c", body=body, depends_on=("a",)))
        rt.spawn(AgentSpec(agent_id="d", body=body, depends_on=("b", "c")))
        rt.run_all()
        # a runs first, then {b, c} in some order, then d.
        assert seen[0] == "a"
        assert set(seen[1:3]) == {"b", "c"}
        assert seen[3] == "d"

    def test_unknown_dependency_raises(self):
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="x", body=_ok_body, depends_on=("missing",)))
        with pytest.raises(PhantomError, match="unknown agent"):
            rt.run_all()

    def test_cycle_detected(self):
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="a", body=_ok_body, depends_on=("b",)))
        rt.spawn(AgentSpec(agent_id="b", body=_ok_body, depends_on=("a",)))
        with pytest.raises(PhantomError, match="cycle"):
            rt.run_all()

    def test_failed_dependency_propagates(self):
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="bad", body=_fail_body))
        rt.spawn(AgentSpec(agent_id="downstream", body=_ok_body, depends_on=("bad",)))
        results = rt.run_all()
        assert results["bad"].status == AgentStatus.FAILED
        assert results["downstream"].status == AgentStatus.FAILED
        assert "upstream" in results["downstream"].error


class TestConcurrencyCap:
    def test_wave_size_cap_enforced(self):
        rt = AgentRuntime(max_concurrent=2)
        for name in "abcd":
            rt.spawn(AgentSpec(agent_id=name, body=_ok_body))
        # All 4 are independent — they all land in wave 0.
        with pytest.raises(PhantomError, match="exceeds max_concurrent"):
            rt.run_all()


class TestEventLog:
    def test_start_and_end_emitted(self):
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="x", body=_ok_body))
        rt.run_all()
        kinds = [e.kind for e in rt.event_log()]
        assert kinds == ["start", "end"]

    def test_error_event_emitted_on_failure(self):
        rt = AgentRuntime()
        rt.spawn(AgentSpec(agent_id="x", body=_fail_body))
        rt.run_all()
        kinds = [e.kind for e in rt.event_log()]
        assert kinds == ["start", "error"]
