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
_CHECKPOINT_RE = None  # populated lazily


def _looks_like_premature_checkpoint(text: str) -> bool:
    """Heuristic: is this text the model stopping mid-task to wait for
    a "go ahead" from the user?

    Triggers on phrases like "I'll run X next", "Let me X now", "Now I'll
    X", "Re-running X", "Installing X now" — short, future/imperfective
    sentences that promise an action but didn't take it.

    Conservative: only fires when the message is short (<400 chars) AND
    contains a forward-looking promise. Long final summaries always
    pass through to the user.
    """
    if not text or len(text) > 400:
        return False
    global _CHECKPOINT_RE
    if _CHECKPOINT_RE is None:
        import re as _re
        _CHECKPOINT_RE = _re.compile(
            r"\b("
            r"i'?ll\s+(now|then|run|start|test|verify|check|install|create|"
            r"add|fix|edit|update|write|read|build|deploy|continue|keep)|"
            r"let me\s+(run|start|test|verify|check|install|fix|continue|"
            r"do that|proceed|do this|try|see)|"
            r"now\s+i'?ll|"
            r"now\s+(running|installing|starting|testing|verifying|"
            r"checking|building|fixing|writing|editing|reading)|"
            r"re-?running|re-?installing|re-?starting|"
            # Present-continuous action verbs at sentence start (start
            # of text OR right after a period+space). Catches "Installing
            # X." / "Starting server." / "Running tests." / "Need X.
            # Installing now."
            r"(?:^|\.\s+)(installing|starting|running|testing|adding|"
            r"creating|writing|editing|fixing|verifying|checking|building)"
            r"\s+(\w+|the\s+\w+|and\s+\w+)|"
            r"installing\s+(\w+\s+)?now|starting\s+(the\s+)?server\s+now|"
            r"running\s+(pytest|the tests|tests)\s+now|"
            r"need(s)?\s+\w+(-\w+)?\.\s*installing|"
            r"will (now|then|run|start|test|verify|install|create|fix)\b"
            r")",
            _re.IGNORECASE | _re.MULTILINE,
        )
    return bool(_CHECKPOINT_RE.search(text))


DEFAULT_SYSTEM_PROMPT = """\
You are Phantom, a local coding agent. You help with software \
engineering tasks: bug fixes, features, refactors, code review, and \
debugging. You operate on the user's actual filesystem via tools, so \
every action has real consequences — be deliberate.

# Act, don't narrate. Don't checkpoint mid-task.

When the user asks you to create, run, install, fix, or build \
something, **call tools to do it**. Do NOT describe the steps you \
would take — actually take them. Saying "I will create app.py" \
without calling write_file is a failure. Call write_file first, then \
report what you did.

**Critical rule: do not stop mid-task and wait for confirmation.** \
Phrases like "I'll re-run pytest now", "Let me install pytest-asyncio", \
"Now starting the server" are TRAPS — if you write them and end the \
turn, you've failed. The user does not want to type "yeah proceed" \
between every step. When you intend to do X next, IMMEDIATELY do X by \
calling the tool. Only stop and return text when the ENTIRE task is \
complete and you're reporting the final state.

Examples of what NOT to do:
  ❌ End turn with "Re-running pytest now."     (just call run_bash for it)
  ❌ End turn with "Let me start the server."   (just call start_server)
  ❌ End turn with "Now I'll fix the schema."   (just call edit_file)
  ❌ End turn with "Installing pytest-asyncio." (just call run_bash)

Examples of what TO do:
  ✓ End turn with: "Server up at http://127.0.0.1:8000/docs. 9/9 tests pass."
  ✓ End turn with: "Bug fixed (was `+ 1` instead of `- 1`); all 4 tests green."
  ✓ End turn with: "Created 9 files; pytest 14/14 ✓; live at http://127.0.0.1:5000."

Concretely:
- "create me a flask app" → call write_file for each file, run_bash \
  for `pip install` and to start the server. Don't paste the code as \
  chat output.
- "fix this bug" → call read_file to see the code, edit_file to \
  patch it, run_bash to verify the fix. Don't write a "you should \
  change line N to..." paragraph.
- "make a directory" → call run_bash with `mkdir -p ...`. Don't say \
  "you can run mkdir -p" without running it.

The user expects results, not advice. If a tool errors, retry with \
corrected arguments. Only after every file is created and every \
command has run, write a 1–3 sentence summary of what changed.

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
        Multi-step coding tasks (build a FastAPI project + run tests +
        fix bugs + start server) routinely need 15-20 rounds. The
        loop-detection logic below catches real loops earlier; the
        round cap is the backstop.
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
    on_text_chunk:
        Optional callable ``(chunk: str) -> None`` invoked once per text
        delta when the provider streams. The chat REPL prints chunks
        live so the user sees tokens as they arrive instead of waiting
        for the full response.
    on_tool_call_approve:
        Optional callable ``(round_idx, tool_call) -> bool``. If set
        and returns False, the tool is NOT executed; instead the agent
        loop records a "user declined" result and lets the model react.
        The chat REPL wires this for /confirm mode (interactive y/n
        before destructive operations).
    """

    provider: Provider
    tools: list[ToolDefinition] = field(default_factory=list)
    system_prompt: str = field(default_factory=lambda: DEFAULT_SYSTEM_PROMPT)
    history: list[ProviderMessage] = field(default_factory=list)
    max_tool_rounds: int = 25
    wall_clock_budget_s: float = 300.0
    on_tool_call: Callable[[int, "ToolCall"], None] | None = None
    on_tool_result: Callable[[int, "ToolCall", str], None] | None = None
    on_text_chunk: Callable[[str], None] | None = None
    on_tool_call_approve: Callable[[int, "ToolCall"], bool] | None = None

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
        recent_calls: list[tuple[str, str]] = []  # (tool_name, args_signature)
        any_tools_used_this_turn = False
        auto_continues_used = 0
        max_auto_continues = 3

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
                # AUTO-CONTINUE: when the model returned text but the
                # text reads like a checkpoint mid-task ("Re-running
                # pytest now.", "Now I'll start the server.", "Installing
                # X now.") AND we already executed tool calls earlier
                # in this turn — that's the model stopping prematurely
                # to wait for confirmation. Push it forward instead of
                # returning to the user.
                if (
                    any_tools_used_this_turn
                    and auto_continues_used < max_auto_continues
                    and _looks_like_premature_checkpoint(response.text or "")
                ):
                    auto_continues_used += 1
                    self.history.append(ProviderMessage(
                        role="assistant", content=response.text or "",
                    ))
                    self.history.append(ProviderMessage(
                        role="user",
                        content=(
                            "Continue. You said you would do something next — "
                            "do it now using tools, without asking for "
                            "permission. Don't summarise; just call the next "
                            "tool. The user is waiting for the FINAL state, "
                            "not progress reports."
                        ),
                    ))
                    continue
                # Final turn — record the assistant message and return.
                self.history.append(ProviderMessage(
                    role="assistant", content=response.text or "",
                ))
                return response.text or ""

            # Tool-call round: record the assistant request, then run
            # each tool and append its result.
            any_tools_used_this_turn = True
            self.history.append(ProviderMessage(
                role="assistant",
                content=response.text or "",
            ))
            for tc in response.tool_calls:
                # Real-loop detection: same tool + same args 3 times in a
                # row means the model is genuinely stuck (model didn't
                # learn from the last result). Bail with a marker.
                # Legitimate multi-step work calls DIFFERENT tools or
                # the same tool with DIFFERENT args, so this never fires.
                args_sig = json.dumps(tc.arguments, sort_keys=True)[:200]
                recent_calls.append((tc.name, args_sig))
                if len(recent_calls) >= 3 and recent_calls[-1] == recent_calls[-2] == recent_calls[-3]:
                    return last_text + (
                        f"\n\n[phantom: detected infinite loop — same tool "
                        f"`{tc.name}` called 3 times with identical args. "
                        f"Stopping to save tokens. Try /reset and rephrasing.]"
                    )

                if self.on_tool_call is not None:
                    try:
                        self.on_tool_call(round_idx, tc)
                    except Exception:
                        pass
                # Approval gate: when /confirm is on, prompt the user
                # before destructive ops. Decline → tool result becomes
                # a clear error the model can react to.
                if self.on_tool_call_approve is not None:
                    try:
                        approved = self.on_tool_call_approve(round_idx, tc)
                    except Exception:
                        approved = True  # never block on a hook error
                    if not approved:
                        tool_result = json.dumps({
                            "error": "user declined this action",
                            "hint": (
                                "The user reviewed the proposed tool call "
                                "and declined. Do not retry the same call. "
                                "Either ask the user what they'd prefer, "
                                "or move on to a different approach."
                            ),
                        })
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
                        continue
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
        # Identity hammer: many open-weight models (qwen-coder leaks
        # "I'm Ling", deepseek leaks "DeepSeek AI") have such strongly
        # trained identities that a single system-prompt instruction
        # gets ignored. Inject a SECOND high-priority system message
        # right before the most recent user turn — closer in attention
        # distance, harder to override.
        identity_hint = getattr(self, "_phantom_identity_hint", None)
        if identity_hint and messages:
            # Find the last user message; insert the hint right before it.
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].role == "user":
                    messages.insert(i, ProviderMessage(
                        role="system", content=identity_hint,
                    ))
                    break
        tools_payload = [t.to_provider_dict() for t in self.tools]
        try:
            # Use streaming when the chat REPL has registered an on_text_chunk
            # callback. The provider returns the same ProviderResponse shape
            # in both modes; streaming just dispatches text deltas live.
            kwargs: dict[str, Any] = {"tools": tools_payload}
            if self.on_text_chunk is not None:
                kwargs["on_chunk"] = self.on_text_chunk
            return self.provider.complete(messages, **kwargs)
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
