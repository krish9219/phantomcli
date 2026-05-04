"""``BrowserAgentRunner`` — runs a browser-use Agent for one task.

Design choices, in order of importance:

1. **The Phantom agent loop is synchronous** (Stage 4 deferred async
   to v4.2). ``browser_use.Agent.run`` is async. We bridge with a
   per-task asyncio loop so callers see a regular blocking function.
2. **One fresh Browser per task.** Sharing a browser session across
   tasks is a footgun — cookies leak, downloads accumulate. Every
   call to :meth:`BrowserAgentRunner.run` starts a fresh
   ``browser_use.Browser`` and closes it on exit.
3. **Headless by default.** Operators who want to watch the browser
   pass ``headless=False`` at construction.
4. **Tasks are bounded.** ``max_steps`` (default 25) and an outer
   wall-clock deadline (default 180 s) prevent runaway browsing.
5. **Failure is structured.** A timeout, an unreachable site, or a
   browser crash all produce a :class:`BrowserTaskResult` with
   ``ok=False`` and a populated ``error``. We never raise from
   :meth:`run`.

Privacy: ``browser-use`` collects telemetry by default. We disable it
in :meth:`run` via the documented env var so a Phantom installation
never silently phones home about user browsing activity.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from phantom.agent.decorator import tool
from phantom.agent.session import ToolDefinition
from phantom.errors import PhantomError

__all__ = ["BrowserAgentRunner", "BrowserTaskResult", "browser_task_tool"]


@dataclass(frozen=True, slots=True)
class BrowserTaskResult:
    """Outcome of one browser task."""

    ok: bool
    final_text: str = ""
    steps: int = 0
    duration_s: float = 0.0
    error: str = ""
    extracted_data: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


class BrowserAgentRunner:
    """Synchronous wrapper around ``browser_use.Agent``.

    Parameters
    ----------
    llm:
        A ``browser_use`` LLM adapter (anything with a ``ainvoke`` /
        ``invoke`` shape that browser-use accepts). Phantom does NOT
        inject its own :class:`Provider` here — browser-use has its
        own agent loop with its own prompt format. Operators wire a
        compatible LLM (``ChatBrowserUse``, ``ChatAnthropic``,
        ``ChatGoogle``) at construction time.
    headless:
        Run Chromium headless. Default True.
    max_steps:
        Per-task step cap. Default 25.
    deadline_s:
        Wall-clock deadline. Default 180 s.
    browser_factory:
        Override for tests. Returns a ``browser_use.Browser`` (or a
        compatible test double).
    agent_factory:
        Override for tests. Receives the task + browser + llm and
        returns a ``browser_use.Agent``-shaped object.
    """

    def __init__(
        self,
        *,
        llm: Any,
        headless: bool = True,
        max_steps: int = 25,
        deadline_s: float = 180.0,
        browser_factory: Any = None,
        agent_factory: Any = None,
    ) -> None:
        if max_steps <= 0:
            raise PhantomError("max_steps must be > 0")
        if deadline_s <= 0:
            raise PhantomError("deadline_s must be > 0")
        self._llm = llm
        self._headless = headless
        self._max_steps = max_steps
        self._deadline = deadline_s
        self._browser_factory = browser_factory
        self._agent_factory = agent_factory

    # ─── public synchronous entry point ────────────────────────────────

    def run(self, task: str) -> BrowserTaskResult:
        """Execute *task* and return the structured outcome.

        Never raises. Failures populate ``BrowserTaskResult.error``.
        """
        if not task or not task.strip():
            return BrowserTaskResult(ok=False, error="task is empty")

        # Telemetry off — Phantom users opt into our own audit log,
        # not a third-party endpoint.
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
        os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "warning")

        try:
            return asyncio.run(self._run_async(task))
        except KeyboardInterrupt:
            return BrowserTaskResult(ok=False, error="interrupted")
        except Exception as exc:
            return BrowserTaskResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _run_async(self, task: str) -> BrowserTaskResult:
        import time

        browser = self._make_browser()
        try:
            agent = self._make_agent(task=task, browser=browser, llm=self._llm)
            started = time.monotonic()
            history = await asyncio.wait_for(
                agent.run(max_steps=self._max_steps),
                timeout=self._deadline,
            )
            duration = time.monotonic() - started
        except asyncio.TimeoutError:
            return BrowserTaskResult(
                ok=False,
                error=f"task exceeded {self._deadline}s deadline",
            )
        finally:
            await self._close_browser(browser)

        return _summarise(history, duration)

    # ─── factories ─────────────────────────────────────────────────────

    def _make_browser(self) -> Any:
        if self._browser_factory is not None:
            return self._browser_factory()
        from browser_use import Browser  # type: ignore[import-not-found]
        return Browser(headless=self._headless)

    def _make_agent(self, *, task: str, browser: Any, llm: Any) -> Any:
        if self._agent_factory is not None:
            return self._agent_factory(task=task, browser=browser, llm=llm)
        from browser_use import Agent  # type: ignore[import-not-found]
        return Agent(task=task, browser=browser, llm=llm)

    @staticmethod
    async def _close_browser(browser: Any) -> None:
        # browser-use exposes either ``aclose`` or ``close`` depending
        # on version; defend against both.
        for attr in ("aclose", "close"):
            close = getattr(browser, attr, None)
            if close is None:
                continue
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # pragma: no cover — best-effort cleanup
                pass
            return


def _summarise(history: Any, duration_s: float) -> BrowserTaskResult:
    """Convert a ``browser_use`` agent history object into our shape.

    The history's API has evolved across versions; we read defensively.
    """
    final_text = ""
    steps = 0
    extracted: list[dict[str, Any]] = []

    # browser-use returns an ``AgentHistoryList`` with helpful methods.
    if hasattr(history, "final_result"):
        try:
            final_text = str(history.final_result() or "")
        except Exception:
            final_text = ""
    if hasattr(history, "extracted_content"):
        try:
            for item in history.extracted_content() or []:
                if isinstance(item, str):
                    extracted.append({"text": item})
                elif isinstance(item, dict):
                    extracted.append(item)
        except Exception:
            pass
    if hasattr(history, "history"):
        try:
            steps = len(history.history)
        except Exception:
            steps = 0

    return BrowserTaskResult(
        ok=bool(final_text) or steps > 0,
        final_text=final_text,
        steps=steps,
        duration_s=round(duration_s, 4),
        extracted_data=extracted,
    )


# ─── tool factory ────────────────────────────────────────────────────────────


def browser_task_tool(
    runner: BrowserAgentRunner,
    *,
    name: str = "browser_task",
    description: str = (
        "Drive a real web browser to complete a task. Use this when the "
        "user asks to look up live information on a website, fill a form, "
        "click through a flow, or extract data from a rendered page. "
        "Returns a JSON object with ok, final_text, steps, duration_s, "
        "extracted_data."
    ),
) -> ToolDefinition:
    """Return a :class:`ToolDefinition` that runs *runner* on the model's task.

    The returned tool is bound to *runner*. Each call spawns a fresh
    browser session inside ``runner.run`` (the runner is stateless
    across calls; it just owns the LLM + headless + timeout config).
    """

    @tool(
        name=name,
        description=description,
        schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Plain-English description of what the browser "
                        "should accomplish, e.g. 'Find the closing price "
                        "of NVDA on yahoo finance'."
                    ),
                },
            },
            "required": ["task"],
        },
    )
    def _handler(args: dict[str, Any]) -> str:
        task = args.get("task", "")
        if not isinstance(task, str):
            return json.dumps({"ok": False, "error": "task must be a string"})
        result = runner.run(task)
        return result.to_json()

    return _handler
