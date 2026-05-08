"""AgentSession — one conversation, end to end.

Sessions are stateful but cheap to construct. Real callers build one
per CLI invocation or per WebChat tab. The agent loop is in
:meth:`AgentSession.respond_to`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from phantom.agent.provider import (
    Provider,
    ProviderMessage,
    ProviderResponse,
    ToolCall,
)
from phantom.errors import PhantomError

__all__ = ["AgentSession", "DEFAULT_SYSTEM_PROMPT", "ToolDefinition"]

log = logging.getLogger(__name__)


# The default system prompt shipped with every AgentSession. It teaches
# the model Phantom's editing philosophy: surgical, targeted changes via
# ``edit_file`` rather than whole-file rewrites via ``write_file``. This
# is the same approach Claude Code, Cursor, and Aider converge on — it
# saves tokens, avoids hallucinated rewrites of unrelated code, and
# keeps git diffs reviewable. Callers that want a different prompt
# (custom personas, domain-specific agents) can pass their own
# ``system_prompt`` to :class:`AgentSession`.
DEFAULT_SYSTEM_PROMPT = """\
You are Phantom, a local coding agent. You help with software \
engineering tasks: bug fixes, features, refactors, code review, and \
debugging. You operate on the user's actual filesystem via tools, so \
every action has real consequences — be deliberate.

# Editing philosophy

When you change a file, prefer `edit_file` (exact-string replacement) \
over `write_file` (whole-file overwrite). Whole-file rewrites destroy \
context, introduce typos in untouched code, and cost tokens — surgical \
edits don't.

For bug fixes specifically:
1. Read the failing code first. `read_file` the exact path from the \
stack trace or error message before changing anything.
2. Find the root cause. Trace the problem to the smallest possible \
cause. Don't guess; verify by reading the surrounding code.
3. Apply the minimum change. Use `edit_file` with `old_string` \
carrying just enough surrounding context to be unique, and \
`new_string` containing only the corrected lines.
4. Don't rewrite working code to "improve it" while you're there. A \
bug fix doesn't need surrounding cleanup. A one-line fix is one \
`edit_file` call — not a `write_file` rewrite of the whole module.
5. Don't add comments explaining what you fixed unless the user asked. \
Git history records what changed; comments rot.

Use `write_file` only when (a) creating a new file, or (b) rewriting \
more than ~80% of an existing one. If you reach for `write_file` to \
fix a bug, stop and reach for `edit_file` instead.

# Output style

Be concise. State what you're about to do in one short sentence before \
each tool call. After the work is done, summarise what changed in 1–3 \
sentences. Don't recap tool output the user can already see.
"""


# A handler takes a JSON args dict and returns a JSON-serialisable
# string (typically JSON-stringified). The agent loop feeds the return
# value back to the model as a `tool` message.
ToolHandler = Callable[[dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One tool the agent can invoke."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def to_provider_dict(self) -> dict[str, Any]:
        """Render in the OpenAI Chat Completions tools shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class AgentSession:
    """One conversation.

    Attributes
    ----------
    provider:
        The :class:`Provider` to call.
    tools:
        Available tools. The session passes their schemas to the
        provider on every call.
    system_prompt:
        Persistent system message prepended to every model call.
    history:
        Conversation history; mutated by :meth:`respond_to`.
    max_tool_rounds:
        Hard cap on tool-call rounds per user turn. Default 25.
        Multi-step coding tasks (read → edit → run tests → fix) and
        ML workflows routinely need more than the original cap of 8.
    """

    provider: Provider
    tools: list[ToolDefinition] = field(default_factory=list)
    system_prompt: str = field(default_factory=lambda: DEFAULT_SYSTEM_PROMPT)
    history: list[ProviderMessage] = field(default_factory=list)
    max_tool_rounds: int = 25

    def __post_init__(self) -> None:
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise PhantomError(f"duplicate tool name in {names}")

    # ─── public API ────────────────────────────────────────────────────

    def respond_to(self, user_message: str) -> str:
        """Add *user_message* to history and run the loop until a final
        text turn. Returns the final assistant text.

        The history is mutated: the user message, every assistant turn
        (including tool-call wrappers), and every tool result are
        appended in order.
        """
        if not user_message:
            raise PhantomError("user_message is empty")
        self.history.append(ProviderMessage(role="user", content=user_message))

        for round_idx in range(self.max_tool_rounds + 1):
            response = self._call_provider()
            if not response.wants_tools:
                # Final turn — record the assistant message and return.
                self.history.append(ProviderMessage(
                    role="assistant", content=response.text or "",
                ))
                return response.text or ""

            # Tool-call round: record the assistant request, then run
            # each tool and append its result.
            self.history.append(ProviderMessage(
                role="assistant",
                content=response.text or "",
                # tool_call_id is not relevant on assistant rows; we
                # could carry the tool-call structure if a future
                # provider needs it.
            ))
            for tc in response.tool_calls:
                tool_result = self._invoke_tool(tc)
                self.history.append(ProviderMessage(
                    role="tool",
                    content=tool_result,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))
            if round_idx >= self.max_tool_rounds:
                # Ran out of rounds. Force the model to summarise next
                # call by clearing the tool offer? For simplicity we
                # just return the partial text + a marker.
                return (response.text or "") + (
                    "\n\n[phantom: tool-round limit reached; "
                    "returning partial result]"
                )
        # Unreachable, but typed paths require a return.
        return ""

    # ─── internals ─────────────────────────────────────────────────────

    def _call_provider(self) -> ProviderResponse:
        messages = [
            ProviderMessage(role="system", content=self.system_prompt),
            *self.history,
        ]
        tools_payload = [t.to_provider_dict() for t in self.tools]
        try:
            return self.provider.complete(messages, tools=tools_payload)
        except PhantomError:
            raise
        except Exception as exc:
            raise PhantomError(f"provider call failed: {exc}") from exc

    def _invoke_tool(self, tc: ToolCall) -> str:
        tool = next((t for t in self.tools if t.name == tc.name), None)
        if tool is None:
            return json.dumps({"error": f"unknown tool {tc.name!r}"})
        try:
            return tool.handler(tc.arguments)
        except Exception as exc:
            log.warning("tool %r raised %s: %s", tc.name, type(exc).__name__, exc)
            return json.dumps({
                "error": f"{type(exc).__name__}: {exc}",
            })
