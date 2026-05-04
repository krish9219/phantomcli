"""
OpenAI-compatible SSE stream parser.

OpenAI-style streams are simpler than Anthropic's: each SSE frame's
`data:` body is a full chat-completion chunk JSON. A single conversation
turn produces many frames, each adding to the `delta` of a single message.
A final `data: [DONE]` terminates.

Shape of each frame's JSON:
  {
    "id": "chatcmpl-...",
    "object": "chat.completion.chunk",
    "created": 1234,
    "model": "gpt-4o",
    "choices": [
      {
        "index": 0,
        "delta": {
          "role": "assistant" | undefined,
          "content": "text..." | undefined,
          "tool_calls": [...] | undefined
        },
        "finish_reason": null | "stop" | "tool_calls"
      }
    ],
    "usage": {...}   // sometimes on the last chunk only
  }

This parser feeds StreamAssembler's `push_delta` directly — that method
already understands OpenAI's delta shape — then returns the assembled
text + tool calls + usage.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Iterable

from omnicli.stream_assembler import StreamAssembler
from omnicli.anthropic_stream import iter_sse_frames   # same SSE framing

log = logging.getLogger("omnicli.openai_stream")


@dataclass
class OpenAIParseResult:
    text:       str
    tool_calls: list[dict]
    usage:      dict = field(default_factory=dict)
    finish_reason: str = ""
    model:      str = ""


def parse_stream(lines: Iterable[str]) -> OpenAIParseResult:
    """Consume an OpenAI-compatible SSE stream and return the assembled
    text + tool_calls + usage."""
    sa = StreamAssembler()
    usage: dict = {}
    finish_reason = ""
    model = ""

    for frame in iter_sse_frames(lines):
        data = frame.data.strip()
        if not data:
            continue
        # Terminator
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue

        if not model and chunk.get("model"):
            model = str(chunk["model"])

        # Top-level usage (some providers emit once at end, Groq emits
        # usage_total as top-level field).
        u = chunk.get("usage")
        if isinstance(u, dict):
            usage = _merge_usage(usage, u)

        for choice in chunk.get("choices", []) or []:
            delta = choice.get("delta", {}) or {}
            # push_delta handles both {"content": "..."} and
            # {"tool_calls": [...]} shapes.
            sa.push_delta(delta)
            fr = choice.get("finish_reason")
            if fr:
                finish_reason = str(fr)

    text, tool_calls, _warnings = sa.finalize()
    return OpenAIParseResult(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=finish_reason,
        model=model,
    )


def parse_bytes(data: bytes) -> OpenAIParseResult:
    return parse_stream(data.decode("utf-8", errors="replace").splitlines())


def _merge_usage(existing: dict, new: dict) -> dict:
    """OpenAI emits integer token counts; sum if both sides have them."""
    out = dict(existing)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if key in new:
            out[key] = int(new[key] or 0)
    return out


__all__ = ["parse_stream", "parse_bytes", "OpenAIParseResult"]
