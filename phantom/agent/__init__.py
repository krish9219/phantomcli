"""Phantom agent loop — the v4 unification.

This is the module that ties Stages 1-8 together. One conversation =
one :class:`AgentSession`. The session owns:

* a :class:`Provider` (the LLM client; OpenAI-compatible by default)
* a :class:`MemoryStore` for namespaced recall
* a :class:`PluginRegistry` for resolving named plugin calls
* a :class:`ChannelRouter` for dispatching outbound messages
* a list of registered tools (including ``run_bash`` which routes
  through the Stage-1 sandbox)

The agent loop's flow:

1. Receive a :class:`ChannelEvent` (from CLI, WebChat, Telegram, …).
2. Build the prompt: system + memory recall + conversation history +
   user message.
3. Call the provider; receive a response that is *either* plain text
   *or* a tool call.
4. If tool call, dispatch it (sandbox / plugin / MCP / memory) and
   feed the result back to the provider for the next turn.
5. When the provider returns a final text, send it via the channel
   router and persist the exchange to memory.

Limit: 8 tool-call rounds per user turn. Anything more is treated as
runaway and the loop returns whatever it has so far.
"""

from __future__ import annotations

from phantom.agent.decorator import tool
from phantom.agent.provider import (
    Provider,
    ProviderMessage,
    ProviderResponse,
    ScriptedProvider,
    ToolCall,
)
from phantom.agent.session import AgentSession, ToolDefinition
from phantom.agent.tools import default_tools

__all__ = [
    "AgentSession",
    "Provider",
    "ProviderMessage",
    "ProviderResponse",
    "ScriptedProvider",
    "ToolCall",
    "ToolDefinition",
    "default_tools",
    "tool",
]
