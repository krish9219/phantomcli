"""
Agent loop — the round-and-retry orchestrator extracted from engine.py.

The engine's `generate_response` still lives in engine.py (it owns the
model-client construction + persona/config assembly) but the REPEATING
LOGIC — "call model, dispatch tool calls, feed results back, repeat
until final answer or round cap" — now lives here where it's testable
in isolation.

Interface (all callables are user-supplied; the loop is pure-ish):

  call_llm(messages, round_index) -> ModelTurn
    Returns a ModelTurn with either `final_text` or `tool_calls`.

  execute_tool(name, args, trust) -> str
    Runs a tool and returns its string output (already through schema
    validation + hooks — the caller wraps omnicli.tool_dispatch.dispatch).

  on_tool_result(name, output) -> str  (optional)
    Lets callers wrap tool outputs before appending to messages (e.g.,
    apply prompt_guard boundary markers for untrusted content).

  on_round_start / on_round_end         (optional observability hooks)

The loop itself:
  1. Call model.
  2. If final_text, done.
  3. If tool_calls, dispatch each, append results, loop.
  4. Cap at `max_rounds` (default 24, env/config override).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("omnicli.agent_loop")


# ─── Types ───────────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    id:   str   = ""
    name: str   = ""
    args: dict  = field(default_factory=dict)


@dataclass
class ModelTurn:
    """One response from the model — either a final text answer or a list
    of tool calls to dispatch."""
    final_text: str              = ""
    tool_calls: list[ToolCall]   = field(default_factory=list)
    usage:      dict             = field(default_factory=dict)  # {prompt_tokens, completion_tokens, model}

    @property
    def is_final(self) -> bool:
        return bool(self.final_text) and not self.tool_calls


@dataclass
class LoopStats:
    rounds:        int            = 0
    tool_calls:    int            = 0
    model_calls:   int            = 0
    finished:      bool           = False
    finish_reason: str            = ""
    total_usage:   dict           = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})


@dataclass
class LoopResult:
    final_text: str
    messages:   list[dict]
    stats:      LoopStats


CallLlmFn      = Callable[[list[dict], int], ModelTurn]
ExecuteToolFn  = Callable[[str, dict, int], str]
FilterOutputFn = Callable[[str, str], str]
ObserverFn     = Callable[[int, dict], None]


# ─── Loop ────────────────────────────────────────────────────────────────────


def run(
    messages:       list[dict],
    call_llm:       CallLlmFn,
    execute_tool:   ExecuteToolFn,
    trust:          int = 2,
    max_rounds:     int = 24,
    on_tool_result: Optional[FilterOutputFn] = None,
    on_round_start: Optional[ObserverFn]     = None,
    on_round_end:   Optional[ObserverFn]     = None,
) -> LoopResult:
    """Drive an agent conversation to completion.

    `messages` is modified incrementally (new messages appended each round).
    The returned LoopResult.messages is the final list.

    Any callable raising propagates — the loop does NOT catch exceptions
    from `call_llm` or `execute_tool`. Callers wrap at the outer level.
    """
    stats = LoopStats()
    msgs = list(messages)   # don't mutate caller's list in place
    max_rounds = max(1, int(max_rounds))

    for round_index in range(max_rounds):
        stats.rounds += 1
        if on_round_start:
            try:
                on_round_start(round_index, {"messages": len(msgs)})
            except Exception as e:
                log.debug("on_round_start hook error (ignored): %s", e)

        t0 = time.perf_counter()
        turn = call_llm(msgs, round_index)
        stats.model_calls += 1
        if turn.usage:
            stats.total_usage["prompt_tokens"]     += int(turn.usage.get("prompt_tokens", 0) or 0)
            stats.total_usage["completion_tokens"] += int(turn.usage.get("completion_tokens", 0) or 0)

        if turn.is_final:
            msgs.append({"role": "assistant", "content": turn.final_text})
            stats.finished = True
            stats.finish_reason = "final_text"
            if on_round_end:
                try:
                    on_round_end(round_index, {"ms": (time.perf_counter() - t0) * 1000,
                                                "final": True})
                except Exception as e:
                    log.debug("on_round_end hook error (ignored): %s", e)
            return LoopResult(final_text=turn.final_text, messages=msgs, stats=stats)

        if not turn.tool_calls:
            # Model returned neither text nor tool calls — treat as end.
            stats.finished = True
            stats.finish_reason = "empty_turn"
            if on_round_end:
                try:
                    on_round_end(round_index, {"ms": (time.perf_counter() - t0) * 1000,
                                                "final": True})
                except Exception:
                    pass
            return LoopResult(final_text=turn.final_text, messages=msgs, stats=stats)

        # Append the assistant's tool-call message (OpenAI-style)
        msgs.append({
            "role": "assistant",
            "content": turn.final_text or None,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": _safe_json(tc.args)}}
                for tc in turn.tool_calls
            ],
        })

        # Dispatch each tool
        for tc in turn.tool_calls:
            output = execute_tool(tc.name, tc.args, trust)
            stats.tool_calls += 1
            if on_tool_result is not None:
                try:
                    output = on_tool_result(tc.name, output)
                except Exception as e:
                    log.debug("on_tool_result filter error (ignored): %s", e)
            msgs.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "name":         tc.name,
                "content":      output,
            })

        if on_round_end:
            try:
                on_round_end(round_index, {"ms": (time.perf_counter() - t0) * 1000,
                                            "final": False,
                                            "tool_calls": len(turn.tool_calls)})
            except Exception:
                pass

    # Hit the round cap without a final answer
    stats.finish_reason = "max_rounds"
    return LoopResult(
        final_text="[loop exceeded max_rounds without final answer]",
        messages=msgs,
        stats=stats,
    )


def _safe_json(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return "{}"


__all__ = ["run", "ToolCall", "ModelTurn", "LoopResult", "LoopStats"]
