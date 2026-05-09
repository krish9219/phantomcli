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
        Hard cap on tool-call rounds per user turn. Default 12.
        Multi-step coding tasks (read → edit → run tests → fix) and
        ML workflows routinely need more than the original cap of 8.
        Going beyond ~12 usually means the model is stuck in a loop;
        prefer to bail out and let the user redirect.
    wall_clock_budget_s:
        Maximum seconds spent in a single ``respond_to`` call. The
        budget is checked between rounds and after every tool result;
        when exceeded the loop returns whatever text was last produced
        plus a "budget exceeded" marker. Default 300s (5 min).
    on_tool_call:
        Optional callable ``(round_idx, tool_call) -> None`` invoked
        before each tool runs. The chat REPL passes a printer that
        shows ``→ tool_name(args)`` so the user sees progress mid-turn
        instead of staring at the spinner for minutes.
    on_tool_result:
        Optional callable ``(round_idx, tool_call, result_str) -> None``
        invoked after each tool returns. Symmetric with on_tool_call.
    """

    provider: Provider
    tools: list[ToolDefinition] = field(default_factory=list)
    system_prompt: str = field(default_factory=lambda: DEFAULT_SYSTEM_PROMPT)
    history: list[ProviderMessage] = field(default_factory=list)
    max_tool_rounds: int = 12
    wall_clock_budget_s: float = 300.0
    on_tool_call: Callable[[int, "ToolCall"], None] | None = None
    on_tool_result: Callable[[int, "ToolCall", str], None] | None = None

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

        Stops on (whichever happens first):
          * a final turn with no tool calls,
          * ``max_tool_rounds`` rounds completed,
          * ``wall_clock_budget_s`` seconds elapsed.
        On the latter two it returns whatever text was last produced
        plus a one-line marker so the user understands why.
        """
        if not user_message:
            raise PhantomError("user_message is empty")
        self.history.append(ProviderMessage(role="user", content=user_message))

        import time as _time
        deadline = _time.monotonic() + max(1.0, self.wall_clock_budget_s)
        last_text = ""

        for round_idx in range(self.max_tool_rounds + 1):
            if _time.monotonic() > deadline:
                return last_text + (
                    f"\n\n[phantom: wall-clock budget "
                    f"({int(self.wall_clock_budget_s)}s) exceeded; "
                    f"returning partial result. Press Enter and ask me "
                    f"to continue if you want me to keep going.]"
                )

            response = self._call_provider()
            last_text = response.text or last_text

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
            ))
            for tc in response.tool_calls:
                if self.on_tool_call is not None:
                    try:
                        self.on_tool_call(round_idx, tc)
                    except Exception:
                        pass
                tool_result = self._invoke_tool(tc)
                if self.on_tool_result is not None:
                    try:
                        self.on_tool_result(round_idx, tc, tool_result)
                    except Exception:
                        pass
                self.history.append(ProviderMessage(
                    role="tool",
                    content=tool_result,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))
                if _time.monotonic() > deadline:
                    return last_text + (
                        f"\n\n[phantom: wall-clock budget exceeded mid-tool-loop; "
                        f"returning partial result.]"
                    )
            if round_idx >= self.max_tool_rounds:
                return (response.text or last_text) + (
                    f"\n\n[phantom: tool-round limit ({self.max_tool_rounds}) "
                    f"reached; returning partial result. The model may be in "
                    f"a loop — try /reset and rephrasing the request.]"
                )
        # Unreachable, but typed paths require a return.
        return last_text

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
