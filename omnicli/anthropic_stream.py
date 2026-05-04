"""
Anthropic-native SSE stream parser.

Anthropic's messages endpoint emits a distinct event stream (NOT
OpenAI-shaped deltas): every chunk is an SSE frame with a `event:` name
and a `data:` JSON body. The sequence is:

    event: message_start         { message }
    event: content_block_start   { content_block, index }
    event: content_block_delta   { delta: {type: text_delta | input_json_delta, ...} }
    event: content_block_delta   ...
    event: content_block_stop    { index }
    event: message_delta         { usage, stop_reason }
    event: message_stop

Text appears as `{"type":"text_delta","text":"..."}` on each delta.
Tool-use appears as `{"type":"input_json_delta","partial_json":"..."}`
with the tool name + id set on the enclosing `content_block_start`'s
`content_block.name` / `.id`.

This module takes a byte stream (or an iterable of lines) and feeds a
StreamAssembler so downstream code (engine, Phantom's tool dispatch)
doesn't care which provider the stream came from.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from omnicli.stream_assembler import StreamAssembler

log = logging.getLogger("omnicli.anthropic_stream")


# ─── Frame iterator ──────────────────────────────────────────────────────────


@dataclass
class SseFrame:
    event: str = ""
    data:  str = ""


def iter_sse_frames(lines: Iterable[str]) -> Iterator[SseFrame]:
    """Yield SseFrame objects from an iterable of raw SSE lines.

    SSE frames are separated by a blank line. Each frame has zero or more
    `event:` / `data:` lines. A single frame can have multiple `data:`
    lines; they're joined with newlines (per the SSE spec)."""
    event, data_parts = "", []
    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")
        if line == "":
            # End of frame
            if event or data_parts:
                yield SseFrame(event=event, data="\n".join(data_parts))
            event, data_parts = "", []
            continue
        if line.startswith(":"):   # comment / keep-alive
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_parts.append(line[len("data:"):].lstrip())
        # Other fields (id:, retry:) ignored
    if event or data_parts:
        yield SseFrame(event=event, data="\n".join(data_parts))


# ─── Parser ──────────────────────────────────────────────────────────────────


@dataclass
class AnthropicParseResult:
    text:       str
    tool_calls: list[dict]
    usage:      dict = field(default_factory=dict)
    stop_reason: str = ""


def parse_stream(lines: Iterable[str]) -> AnthropicParseResult:
    """Drive a StreamAssembler through an Anthropic SSE stream.

    Returns the reassembled text + tool calls + usage/stop_reason.
    """
    sa = StreamAssembler()
    usage: dict = {}
    stop_reason = ""
    # Map: content-block index → (tool_use_id, tool_name)
    block_tool: dict[int, tuple[str, str]] = {}

    for frame in iter_sse_frames(lines):
        if not frame.data:
            continue
        try:
            payload = json.loads(frame.data)
        except json.JSONDecodeError:
            continue
        ev = frame.event or payload.get("type", "")
        # ── content_block_start: record tool_use block metadata
        if ev == "content_block_start":
            idx = int(payload.get("index", 0))
            cb = payload.get("content_block", {}) or {}
            cb_type = cb.get("type", "")
            if cb_type == "tool_use":
                block_tool[idx] = (cb.get("id", ""), cb.get("name", ""))
                sa.push_tool_delta(index=idx, id_=cb.get("id", ""),
                                   name=cb.get("name", ""))
            # text blocks need no start-time init
        elif ev == "content_block_delta":
            idx   = int(payload.get("index", 0))
            delta = payload.get("delta", {}) or {}
            dtype = delta.get("type", "")
            if dtype == "text_delta":
                sa.push_text(str(delta.get("text", "")))
            elif dtype == "input_json_delta":
                sa.push_tool_delta(index=idx,
                                   args_chunk=str(delta.get("partial_json", "")))
        elif ev == "content_block_stop":
            pass   # nothing to flush — assembler handles on finalize
        elif ev == "message_delta":
            # Usage + stop_reason arrive here
            u = payload.get("usage", {}) or {}
            if u:
                # Anthropic sends {input_tokens, output_tokens}; normalise
                if "input_tokens" in u or "output_tokens" in u:
                    usage["prompt_tokens"]     = int(u.get("input_tokens", 0) or 0) + usage.get("prompt_tokens", 0)
                    usage["completion_tokens"] = int(u.get("output_tokens", 0) or 0) + usage.get("completion_tokens", 0)
                else:
                    usage.update(u)
            delta = payload.get("delta", {}) or {}
            if delta.get("stop_reason"):
                stop_reason = str(delta.get("stop_reason"))
        elif ev == "message_start":
            m = payload.get("message", {}) or {}
            u = m.get("usage", {}) or {}
            if u:
                usage["prompt_tokens"]     = int(u.get("input_tokens", 0) or 0)
                usage["completion_tokens"] = int(u.get("output_tokens", 0) or 0)
        elif ev == "message_stop":
            pass
        elif ev == "error":
            # Pass through — caller sees text but can inspect stop_reason
            stop_reason = stop_reason or "error"

    text, tool_calls, _warnings = sa.finalize()
    return AnthropicParseResult(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        stop_reason=stop_reason,
    )


def parse_bytes(data: bytes) -> AnthropicParseResult:
    """Convenience: parse a complete captured SSE response body."""
    text = data.decode("utf-8", errors="replace")
    return parse_stream(text.splitlines())


__all__ = ["parse_stream", "parse_bytes", "AnthropicParseResult",
           "iter_sse_frames", "SseFrame"]
