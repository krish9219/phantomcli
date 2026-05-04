"""Tests for :mod:`phantom.browser.runner`.

We use stub browser/agent factories so the test suite never spawns a
real Chromium. A separate gated test (``test_runner_real.py``) drives
a live headless browser when explicitly enabled.
"""

from __future__ import annotations

import json

import pytest

from phantom.agent.session import ToolDefinition
from phantom.browser import (
    BrowserAgentRunner,
    BrowserTaskResult,
    browser_task_tool,
)
from phantom.errors import PhantomError


# ─── fakes ───────────────────────────────────────────────────────────────────


class _FakeHistory:
    def __init__(self, final="ok", extracted=None, steps=3):
        self._final = final
        self._extracted = extracted or []
        self.history = [object()] * steps

    def final_result(self):
        return self._final

    def extracted_content(self):
        return self._extracted


class _FakeAgent:
    def __init__(self, *, task, browser, llm):
        self.task = task
        self.browser = browser
        self.llm = llm

    async def run(self, *, max_steps=25):
        return _FakeHistory(final=f"completed: {self.task}")


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _SlowAgent(_FakeAgent):
    async def run(self, *, max_steps=25):
        import asyncio
        await asyncio.sleep(2.0)
        return _FakeHistory()


class _BoomAgent(_FakeAgent):
    async def run(self, *, max_steps=25):
        raise RuntimeError("page crashed")


# ─── construction ────────────────────────────────────────────────────────────


class TestConstruction:
    def test_max_steps_must_be_positive(self):
        with pytest.raises(PhantomError, match="max_steps"):
            BrowserAgentRunner(llm=None, max_steps=0)

    def test_deadline_must_be_positive(self):
        with pytest.raises(PhantomError, match="deadline_s"):
            BrowserAgentRunner(llm=None, deadline_s=0)


# ─── happy path ──────────────────────────────────────────────────────────────


class TestRunHappyPath:
    def test_round_trip(self):
        b = _FakeBrowser()
        runner = BrowserAgentRunner(
            llm=object(),
            browser_factory=lambda: b,
            agent_factory=_FakeAgent,
        )
        result = runner.run("find the price of NVDA")
        assert isinstance(result, BrowserTaskResult)
        assert result.ok
        assert "find the price of NVDA" in result.final_text
        assert result.steps == 3
        # Browser was closed on exit.
        assert b.closed

    def test_extracted_content_propagates(self):
        class _ExtractAgent(_FakeAgent):
            async def run(self, *, max_steps=25):
                return _FakeHistory(
                    final="done",
                    extracted=[{"price": 102.4}, "raw text"],
                )

        runner = BrowserAgentRunner(
            llm=object(),
            browser_factory=_FakeBrowser,
            agent_factory=_ExtractAgent,
        )
        result = runner.run("extract")
        # str items are wrapped in {text: ...}.
        assert {"price": 102.4} in result.extracted_data
        assert {"text": "raw text"} in result.extracted_data


# ─── failure paths ───────────────────────────────────────────────────────────


class TestRunFailures:
    def test_empty_task_returns_failure(self):
        runner = BrowserAgentRunner(
            llm=None,
            browser_factory=_FakeBrowser, agent_factory=_FakeAgent,
        )
        result = runner.run("   ")
        assert not result.ok
        assert "empty" in result.error

    def test_agent_exception_caught(self):
        runner = BrowserAgentRunner(
            llm=None,
            browser_factory=_FakeBrowser,
            agent_factory=_BoomAgent,
        )
        result = runner.run("trigger a crash")
        assert not result.ok
        assert "RuntimeError" in result.error
        assert "page crashed" in result.error

    def test_deadline_exceeded(self):
        runner = BrowserAgentRunner(
            llm=None,
            deadline_s=0.5,
            browser_factory=_FakeBrowser,
            agent_factory=_SlowAgent,
        )
        result = runner.run("slow task")
        assert not result.ok
        assert "deadline" in result.error.lower()

    def test_browser_closed_even_on_failure(self):
        b = _FakeBrowser()
        runner = BrowserAgentRunner(
            llm=None,
            browser_factory=lambda: b,
            agent_factory=_BoomAgent,
        )
        runner.run("x")
        assert b.closed


# ─── tool factory ────────────────────────────────────────────────────────────


class TestBrowserTaskTool:
    def test_returns_tool_definition(self):
        runner = BrowserAgentRunner(
            llm=None,
            browser_factory=_FakeBrowser, agent_factory=_FakeAgent,
        )
        td = browser_task_tool(runner)
        assert isinstance(td, ToolDefinition)
        assert td.name == "browser_task"
        assert "task" in td.input_schema["required"]

    def test_handler_round_trips_through_runner(self):
        runner = BrowserAgentRunner(
            llm=None,
            browser_factory=_FakeBrowser, agent_factory=_FakeAgent,
        )
        td = browser_task_tool(runner)
        out_json = td.handler({"task": "go to example.com"})
        out = json.loads(out_json)
        assert out["ok"]
        assert "go to example.com" in out["final_text"]

    def test_handler_rejects_non_string_task(self):
        runner = BrowserAgentRunner(
            llm=None,
            browser_factory=_FakeBrowser, agent_factory=_FakeAgent,
        )
        td = browser_task_tool(runner)
        out = json.loads(td.handler({"task": 42}))
        assert out["ok"] is False
        assert "string" in out["error"]

    def test_custom_name_and_description(self):
        runner = BrowserAgentRunner(
            llm=None,
            browser_factory=_FakeBrowser, agent_factory=_FakeAgent,
        )
        td = browser_task_tool(
            runner, name="web_navigate",
            description="Custom desc.",
        )
        assert td.name == "web_navigate"
        assert td.description == "Custom desc."


# ─── result helpers ──────────────────────────────────────────────────────────


class TestBrowserTaskResult:
    def test_to_json_round_trips(self):
        r = BrowserTaskResult(ok=True, final_text="x", steps=2, duration_s=0.5)
        d = json.loads(r.to_json())
        assert d == {
            "ok": True, "final_text": "x", "steps": 2,
            "duration_s": 0.5, "error": "", "extracted_data": [],
        }
