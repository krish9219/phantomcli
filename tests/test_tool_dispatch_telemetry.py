"""Tests that tool_dispatch emits the expected OTel spans + metrics."""
from __future__ import annotations

import pytest

from omnicli import tool_dispatch, telemetry


@pytest.fixture(autouse=True)
def _otel():
    telemetry.shutdown()
    telemetry.clear_metrics()
    ok = telemetry.init(exporter="memory")
    if not ok:
        pytest.skip("opentelemetry not installed")
    yield
    telemetry.shutdown()
    telemetry.clear_metrics()


def _spans():
    exp = telemetry.memory_exporter()
    return list(exp.get_finished_spans()) if exp else []


class TestSpansOnSuccess:
    def test_span_created_for_happy_dispatch(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash",
                            lambda cmd, trust, on_output=None: "ok output")
        out = tool_dispatch.dispatch("run_bash", {"command": "ls"}, trust=3)
        assert out == "ok output"
        names = [s.name for s in _spans()]
        assert "phantom.tool.call" in names

    def test_span_records_tool_and_trust(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash",
                            lambda cmd, trust, on_output=None: "ok")
        tool_dispatch.dispatch("run_bash", {"command": "ls"}, trust=3)
        tool_span = next(s for s in _spans() if s.name == "phantom.tool.call")
        assert tool_span.attributes["tool"]  == "run_bash"
        assert tool_span.attributes["trust"] == 3

    def test_record_tool_call_span_also_emitted(self, monkeypatch):
        """tool_dispatch emits BOTH a wrapping span and a dedicated
        phantom.tool.call summary span via record_tool_call."""
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash",
                            lambda *a, **k: "ok")
        tool_dispatch.dispatch("run_bash", {"command": "ls"}, trust=2)
        spans = _spans()
        tool_call_spans = [s for s in spans if s.name == "phantom.tool.call"]
        # At least one span with ok=True attribute exists
        ok_vals = [s.attributes.get("ok") for s in tool_call_spans if "ok" in s.attributes]
        assert True in ok_vals


class TestSpanOnFailure:
    def test_schema_rejection_marked_not_ok(self):
        tool_dispatch.dispatch("run_bash", {}, trust=3)   # missing 'command'
        spans = _spans()
        tool_call_spans = [s for s in spans if s.name == "phantom.tool.call"]
        ok_vals = [s.attributes.get("ok") for s in tool_call_spans if "ok" in s.attributes]
        assert False in ok_vals
        # Error attribute is set
        err_spans = [s for s in tool_call_spans
                     if "error" in s.attributes and s.attributes.get("ok") is False]
        assert err_spans
        assert "INVALID_TOOL_ARGS" in err_spans[0].attributes["error"]

    def test_unknown_tool_marked_not_ok(self):
        tool_dispatch.dispatch("nonexistent_tool", {}, trust=3)
        tool_call_spans = [s for s in _spans() if s.name == "phantom.tool.call"]
        ok_vals = [s.attributes.get("ok") for s in tool_call_spans if "ok" in s.attributes]
        assert False in ok_vals


class TestMetricsEmitted:
    def test_duration_metric_recorded(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash", lambda *a, **k: "ok")
        tool_dispatch.dispatch("run_bash", {"command": "ls"}, trust=3)
        metrics = telemetry.metrics_snapshot()
        names = [m["name"] for m in metrics]
        assert "phantom.tool.duration_ms" in names

    def test_metric_labels(self, monkeypatch):
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash", lambda *a, **k: "ok")
        tool_dispatch.dispatch("run_bash", {"command": "pwd"}, trust=3)
        metrics = telemetry.metrics_snapshot()
        m = next(m for m in metrics if m["name"] == "phantom.tool.duration_ms")
        assert m["labels"]["tool"] == "run_bash"
        assert m["labels"]["ok"] == "true"


class TestTelemetryDisabledStillWorks:
    def test_no_crash_when_telemetry_off(self, monkeypatch):
        telemetry.shutdown()  # telemetry off
        from omnicli import engine
        monkeypatch.setattr(engine, "execute_bash", lambda *a, **k: "ok")
        # Must not raise
        out = tool_dispatch.dispatch("run_bash", {"command": "ls"}, trust=3)
        assert out == "ok"
