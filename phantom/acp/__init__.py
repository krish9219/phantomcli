"""Phantom ACP — Agent Communication Protocol.

ACP describes how a parent agent spawns child agents, streams events
between them, and aggregates their results. Stage 4 ships a
**single-process** ACP runtime — children run in the same Python
process, isolated by the Stage-1 sandbox when they invoke shell tools.
A future stage may add cross-process spawning over MCP.

Public surface:

* :class:`AgentSpec`   — declarative description of a child agent.
* :class:`AgentResult` — what a finished agent reports.
* :class:`AgentRuntime` — the parent-side coordinator.
* :class:`AgentMessage` / :class:`AgentEvent` — wire types.
"""

from __future__ import annotations

from phantom.acp.runtime import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentRuntime,
    AgentSpec,
    AgentStatus,
)

__all__ = [
    "AgentEvent",
    "AgentMessage",
    "AgentResult",
    "AgentRuntime",
    "AgentSpec",
    "AgentStatus",
]
