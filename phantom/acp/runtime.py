"""ACP runtime — single-process child-agent coordinator.

The parent spawns children with :meth:`AgentRuntime.spawn`. Each child
runs synchronously to completion (Stage-4 cut) and returns an
:class:`AgentResult`. Streaming events between parent and child use
the :class:`AgentEvent` type so a future async runtime can replace the
sync core without changing the public API.

Dependency waves
----------------

The runtime supports declarative dependencies between children: spawn
N children, each declaring which other children it depends on, and
:meth:`AgentRuntime.run_all` runs them in topological order, parallel
within a wave (sync today; async-friendly tomorrow).

Mass-spawn safety
-----------------

The runtime caps total concurrent children at
``AgentRuntime.max_concurrent`` (default 4). Exceeding the cap raises
:class:`phantom.errors.PhantomError`. This protects the host from a
runaway agent that decides to ``spawn`` 10 000 children.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from phantom.errors import PhantomError

__all__ = [
    "AgentEvent",
    "AgentMessage",
    "AgentResult",
    "AgentRuntime",
    "AgentSpec",
    "AgentStatus",
]


# ─── data types ───────────────────────────────────────────────────────────────


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """Parent → child or child → parent message."""

    agent_id: str
    role: str  # "user" | "assistant" | "tool"
    content: str


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One event emitted during agent execution.

    ``kind`` is one of ``"start"``, ``"message"``, ``"end"``, ``"error"``.
    ``payload`` carries the event-specific data; consumers must check
    ``kind`` before reading typed fields.
    """

    kind: str
    agent_id: str
    payload: dict[str, Any] = field(default_factory=dict)


# Body of a child agent. Receives the spec + a callable for emitting
# events (the parent attaches a sink); returns the final dict result.
AgentBody = Callable[["AgentSpec", Callable[[AgentEvent], None]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Declarative description of a child to spawn.

    Attributes
    ----------
    agent_id:
        Caller-chosen identifier. Must be unique within an
        :class:`AgentRuntime`.
    body:
        Callable that performs the agent's work and returns a dict.
    depends_on:
        Tuple of other ``agent_id`` values that must complete before
        this agent starts. Detected as a wave by the topological
        scheduler.
    inputs:
        Free-form input dict the body receives.
    """

    agent_id: str
    body: AgentBody
    depends_on: tuple[str, ...] = ()
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentResult:
    """The outcome of one child agent."""

    agent_id: str
    status: AgentStatus
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ─── runtime ──────────────────────────────────────────────────────────────────


@dataclass
class AgentRuntime:
    """Single-process child-agent coordinator.

    Stage 4 runs everything synchronously. The interface allows future
    stages to drop in an async executor without changing callers.
    """

    max_concurrent: int = 4
    _specs: dict[str, AgentSpec] = field(default_factory=dict)
    _events: list[AgentEvent] = field(default_factory=list)

    # ─── spawn / inspect ───────────────────────────────────────────────

    def spawn(self, spec: AgentSpec) -> None:
        """Register *spec*. Does not start the agent — call
        :meth:`run_all` to drain the queue.
        """
        if spec.agent_id in self._specs:
            raise PhantomError(f"duplicate agent_id {spec.agent_id!r}")
        if len(self._specs) >= 1024:
            raise PhantomError("too many spawned agents (cap 1024)")
        self._specs[spec.agent_id] = spec

    def event_log(self) -> list[AgentEvent]:
        return list(self._events)

    # ─── execution ─────────────────────────────────────────────────────

    def run_all(self) -> dict[str, AgentResult]:
        """Run every spawned agent in dependency order.

        Returns a mapping ``agent_id → AgentResult`` covering every
        agent that ran (failed children are reported with
        ``status=FAILED``, not raised).

        Raises :class:`PhantomError` if dependencies form a cycle or
        reference an unknown agent.
        """
        order = self._topological_order()
        results: dict[str, AgentResult] = {}

        for wave in order:
            if len(wave) > self.max_concurrent:
                # Stage-4 cut: enforce the cap at wave granularity. A
                # future async runtime can pipeline waves to honour the
                # cap globally, not per-wave.
                raise PhantomError(
                    f"wave size {len(wave)} exceeds max_concurrent {self.max_concurrent}"
                )
            for agent_id in wave:
                spec = self._specs[agent_id]
                # If any dependency failed, mark this agent failed too.
                failed_dep = next(
                    (d for d in spec.depends_on if results.get(d) and
                     results[d].status == AgentStatus.FAILED),
                    None,
                )
                if failed_dep is not None:
                    results[agent_id] = AgentResult(
                        agent_id=agent_id,
                        status=AgentStatus.FAILED,
                        error=f"upstream dependency {failed_dep!r} failed",
                    )
                    continue
                self._emit(AgentEvent(kind="start", agent_id=agent_id))
                try:
                    out = spec.body(spec, self._emit)
                    if not isinstance(out, dict):
                        raise PhantomError(
                            f"agent {agent_id!r} body must return a dict"
                        )
                    results[agent_id] = AgentResult(
                        agent_id=agent_id,
                        status=AgentStatus.COMPLETED,
                        output=out,
                    )
                    self._emit(AgentEvent(
                        kind="end", agent_id=agent_id, payload={"output": out},
                    ))
                except Exception as exc:
                    results[agent_id] = AgentResult(
                        agent_id=agent_id,
                        status=AgentStatus.FAILED,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    self._emit(AgentEvent(
                        kind="error", agent_id=agent_id, payload={
                            "type": type(exc).__name__, "message": str(exc),
                        },
                    ))
        return results

    # ─── helpers ───────────────────────────────────────────────────────

    def _emit(self, evt: AgentEvent) -> None:
        self._events.append(evt)

    def _topological_order(self) -> list[list[str]]:
        """Return waves of agent IDs, each wave runnable in parallel."""
        in_deg: dict[str, int] = {aid: 0 for aid in self._specs}
        edges: dict[str, list[str]] = defaultdict(list)
        for aid, spec in self._specs.items():
            for dep in spec.depends_on:
                if dep not in self._specs:
                    raise PhantomError(
                        f"agent {aid!r} depends on unknown agent {dep!r}"
                    )
                edges[dep].append(aid)
                in_deg[aid] += 1

        waves: list[list[str]] = []
        ready: deque[str] = deque(sorted(a for a, d in in_deg.items() if d == 0))
        scheduled = 0
        while ready:
            wave = sorted(ready)
            waves.append(wave)
            ready.clear()
            for aid in wave:
                scheduled += 1
                for downstream in edges[aid]:
                    in_deg[downstream] -= 1
                    if in_deg[downstream] == 0:
                        ready.append(downstream)
        if scheduled != len(self._specs):
            raise PhantomError("agent dependency cycle detected")
        return waves
