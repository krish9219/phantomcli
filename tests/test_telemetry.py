"""Tests for telemetry — OpenTelemetry wrappers with in-memory exporter."""
from __future__ import annotations

import pytest

from omnicli import telemetry


@pytest.fixture(autouse=True)
def _init_and_teardown():
    telemetry.shutdown()
    telemetry.clear_metrics()
    ok = telemetry.init(exporter="memory")
    if not ok:
        pytest.skip("opentelemetry not installed")
    yield
    telemetry.shutdown()
    telemetry.clear_metrics()


def _spans() -> list:
    exp = telemetry.memory_exporter()
    return list(exp.get_finished_spans()) if exp else []


class TestInitIdempotency:
    def test_double_init_is_safe(self):
        assert telemetry.init() is True
        assert telemetry.init() is True


class TestSpan:
    def test_basic_span_creates_record(self):
        with telemetry.span("test.op", foo="bar"):
            pass
        spans = _spans()
        assert len(spans) == 1
        assert spans[0].name == "test.op"
        assert spans[0].attributes.get("foo") == "bar"

    def test_nested_spans(self):
        with telemetry.span("outer"):
            with telemetry.span("inner"):
                pass
        spans = _spans()
        names = [s.name for s in spans]
        # Children export before parents in simple processor
        assert "outer" in names
        assert "inner" in names

    def test_non_primitive_attr_coerced(self):
        with telemetry.span("coerce", obj={"a": 1}):
            pass
        spans = _spans()
        # str() of dict is set
        assert spans[0].attributes.get("obj") == "{'a': 1}"


class TestModelCall:
    def test_records_full_attributes(self):
        telemetry.record_model_call(
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            duration_ms=250.0,
            provider="openai",
        )
        spans = _spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "phantom.model.call"
        assert s.attributes["model"] == "gpt-4o"
        assert s.attributes["provider"] == "openai"
        assert s.attributes["prompt_tokens"] == 100
        assert s.attributes["completion_tokens"] == 50
        assert s.attributes["total_tokens"] == 150
        assert s.attributes["duration_ms"] == 250.0

    def test_error_field_recorded(self):
        telemetry.record_model_call(model="x", error="timeout after 30s")
        spans = _spans()
        assert spans[0].attributes["error"] == "timeout after 30s"


class TestToolCall:
    def test_records_ok_true(self):
        telemetry.record_tool_call(tool="run_bash", trust=3, ok=True, duration_ms=12.0)
        spans = _spans()
        assert spans[0].name == "phantom.tool.call"
        assert spans[0].attributes["tool"] == "run_bash"
        assert spans[0].attributes["ok"] is True

    def test_records_failure(self):
        telemetry.record_tool_call(tool="write_file", trust=3, ok=False,
                                   error="path outside sandbox")
        spans = _spans()
        assert spans[0].attributes["ok"] is False
        assert "sandbox" in spans[0].attributes["error"]


class TestHook:
    def test_blocking_hook_recorded(self):
        telemetry.record_hook(event="PreToolUse", allowed=False, exit_code=2,
                              duration_ms=40.0)
        spans = _spans()
        assert spans[0].name == "phantom.hook.fire"
        assert spans[0].attributes["allowed"] is False
        assert spans[0].attributes["exit_code"] == 2

    def test_allowing_hook_recorded(self):
        telemetry.record_hook(event="PostToolUse", allowed=True, exit_code=0)
        spans = _spans()
        assert spans[0].attributes["allowed"] is True


class TestMetrics:
    def test_record_metric_appends(self):
        telemetry.record_metric("tokens.total", 1234, model="gpt-4o")
        buf = telemetry.metrics_snapshot()
        assert len(buf) == 1
        assert buf[0]["name"] == "tokens.total"
        assert buf[0]["value"] == 1234.0
        assert buf[0]["labels"]["model"] == "gpt-4o"

    def test_ring_buffer_caps(self):
        # Push many metrics; buffer should cap
        for i in range(15_000):
            telemetry.record_metric("x", i)
        buf = telemetry.metrics_snapshot()
        assert len(buf) <= 10_000

    def test_clear_metrics(self):
        telemetry.record_metric("a", 1)
        telemetry.clear_metrics()
        assert telemetry.metrics_snapshot() == []


class TestDisabledNoop:
    def test_span_is_noop_when_shutdown(self):
        telemetry.shutdown()
        # Span usage with telemetry off must not crash
        with telemetry.span("noop-test", x=1) as s:
            s.set_attribute("ignored", "ok")

    def test_record_model_call_noop_when_shutdown(self):
        telemetry.shutdown()
        # No exception even though there's no exporter
        telemetry.record_model_call(model="x")


class TestSpanCountsForSyntheticConversation:
    def test_typical_round(self):
        """Simulate a single agent round: 1 model call + 2 tool calls + 2 hooks."""
        telemetry.record_model_call(model="claude-opus-4-7",
                                    prompt_tokens=500, completion_tokens=200,
                                    duration_ms=1800)
        telemetry.record_hook(event="PreToolUse", allowed=True, exit_code=0)
        telemetry.record_tool_call(tool="run_bash", trust=3, ok=True, duration_ms=85)
        telemetry.record_hook(event="PostToolUse", allowed=True, exit_code=0)
        telemetry.record_tool_call(tool="write_file", trust=3, ok=True, duration_ms=15)
        spans = _spans()
        by_name = {}
        for s in spans:
            by_name[s.name] = by_name.get(s.name, 0) + 1
        assert by_name["phantom.model.call"] == 1
        assert by_name["phantom.tool.call"]  == 2
        assert by_name["phantom.hook.fire"]  == 2
