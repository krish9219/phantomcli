"""
Robust streaming delta assembler for tool-calling chat completions.

Provider streams (OpenAI-compatible, Anthropic, Gemini, ...) emit deltas
that can split a single tool call's JSON arguments across many chunks.
The old engine.py parser concatenated raw deltas then json.loads()'d at
the end — which fails silently when:

  * a tool-call arg JSON is mid-way (partial → json.loads errors)
  * a chunk boundary splits a UTF-8 continuation byte
  * the model emits multiple tool calls in one turn (indexed deltas)
  * the provider sends a trailing `done` without a close brace

This module is a state machine that processes incremental deltas and
returns a consistent view on demand:

  StreamAssembler()
    .push_delta(delta_dict)   # or .push_text(str) for bare text streams
    .push_tool_delta(index, name=None, args_chunk=None, id_=None)
    .finalize()               # returns (text, tool_calls, warnings)
    .text                     # current accumulated text
    .tool_calls               # current list of {id, name, args} (args as dict or None)
    .pending                  # indices with malformed partial JSON still buffered

Properties:
  * Tolerates out-of-order `index` fields
  * Salvages malformed JSON via a best-effort repair (balance braces/
    quotes) so partial tool calls produce an args dict rather than None
  * Decodes UTF-8 incrementally so multi-byte chars across chunks don't
    corrupt the text
"""
from __future__ import annotations

import codecs
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("omnicli.stream_assembler")


@dataclass
class ToolCallPartial:
    id:          str = ""
    name:        str = ""
    args_raw:    str = ""
    args_parsed: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "id":   self.id,
            "name": self.name,
            "args": self.args_parsed if self.args_parsed is not None else self.args_raw,
        }


class StreamAssembler:
    """Accumulates streaming deltas and produces a consistent final view.

    Instances are single-use per completion stream. Create a new one per
    request.
    """

    def __init__(self):
        self.text_chunks:      list[str] = []
        self._utf8_decoder                 = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._tool_calls:      dict[int, ToolCallPartial] = {}
        self.warnings:         list[str] = []
        self._finalized        = False

    # ─── Text stream ────────────────────────────────────────────────────────

    def push_text(self, chunk: str | bytes) -> None:
        """Append a text chunk. Bytes are UTF-8 decoded incrementally so a
        multi-byte character split across chunks doesn't corrupt."""
        if self._finalized:
            raise RuntimeError("push_text after finalize()")
        if isinstance(chunk, bytes):
            self.text_chunks.append(self._utf8_decoder.decode(chunk))
        else:
            self.text_chunks.append(chunk)

    @property
    def text(self) -> str:
        return "".join(self.text_chunks)

    # ─── Tool-call stream ───────────────────────────────────────────────────

    def push_tool_delta(
        self,
        index: int = 0,
        id_:   str | None = None,
        name:  str | None = None,
        args_chunk: str | None = None,
    ) -> None:
        """Incremental update for a single tool call. OpenAI-compatible
        providers address each tool call by integer `index` and emit
        successive deltas with non-None subsets of the fields."""
        if self._finalized:
            raise RuntimeError("push_tool_delta after finalize()")
        tc = self._tool_calls.setdefault(index, ToolCallPartial())
        if id_:
            tc.id = id_
        if name:
            tc.name = name
        if args_chunk:
            tc.args_raw += args_chunk

    def push_delta(self, delta: dict) -> None:
        """Convenience: consume an OpenAI-compatible `delta` dict.

        Handles the two common shapes:
          {"content": "text..."}
          {"tool_calls": [{"index":0,"id":...,"function":{"name":..., "arguments":...}}]}
        """
        if self._finalized:
            raise RuntimeError("push_delta after finalize()")
        if not isinstance(delta, dict):
            return
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.push_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    t = part.get("text") or ""
                    if t:
                        self.push_text(t)
        for tc in delta.get("tool_calls", []) or []:
            if not isinstance(tc, dict):
                continue
            idx  = int(tc.get("index", 0))
            id_  = tc.get("id")
            fn   = tc.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            args = fn.get("arguments") if isinstance(fn, dict) else None
            self.push_tool_delta(index=idx, id_=id_, name=name, args_chunk=args)

    # ─── Finalize ───────────────────────────────────────────────────────────

    def finalize(self) -> tuple[str, list[dict], list[str]]:
        """Close the stream. Flushes the UTF-8 decoder, parses tool-call
        JSON arguments (repairing if possible), and returns
        (text, tool_calls, warnings)."""
        if self._finalized:
            return self.text, [t.as_dict() for t in self._ordered()], list(self.warnings)
        # Flush decoder — any trailing incomplete UTF-8 bytes become U+FFFD.
        try:
            tail = self._utf8_decoder.decode(b"", final=True)
            if tail:
                self.text_chunks.append(tail)
        except Exception as e:
            self.warnings.append(f"utf8 flush error: {e}")

        for idx, tc in self._tool_calls.items():
            if not tc.args_raw:
                tc.args_parsed = {}
                continue
            try:
                tc.args_parsed = json.loads(tc.args_raw)
            except json.JSONDecodeError as e:
                repaired = _repair_partial_json(tc.args_raw)
                if repaired is not None:
                    tc.args_parsed = repaired
                    self.warnings.append(
                        f"tool_call[{idx}] args repaired from partial JSON"
                    )
                else:
                    tc.args_parsed = None
                    self.warnings.append(
                        f"tool_call[{idx}] args unparseable: {e.msg}"
                    )
        self._finalized = True
        return self.text, [t.as_dict() for t in self._ordered()], list(self.warnings)

    # ─── Read-only views ────────────────────────────────────────────────────

    @property
    def tool_calls(self) -> list[dict]:
        return [t.as_dict() for t in self._ordered()]

    @property
    def pending(self) -> list[int]:
        """Indices whose args could not be parsed (neither strict JSON
        nor repair worked). Empty until finalize() has run."""
        if not self._finalized:
            return []
        return [i for i, t in self._tool_calls.items() if t.args_parsed is None]

    def _ordered(self) -> list[ToolCallPartial]:
        return [self._tool_calls[i] for i in sorted(self._tool_calls.keys())]


# ─── Partial-JSON repair ────────────────────────────────────────────────────


# When a tool-call stream is truncated mid-generation, the JSON emitted so
# far is usually structurally valid except for missing closing braces,
# quotes, or a trailing comma. We perform a conservative repair: balance
# braces/brackets, close an open string, drop trailing comma.
_PARTIAL_JSON_MAX_REPAIR_CHARS = 200_000


def _repair_partial_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    if len(raw) > _PARTIAL_JSON_MAX_REPAIR_CHARS:
        return None
    s = raw.strip()
    if not s.startswith("{"):
        return None  # we only repair object args, not arrays

    # Track quote/escape state, count unclosed braces and brackets.
    in_str = False
    escape = False
    braces = 0
    brackets = 0
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        elif ch == "[":
            brackets += 1
        elif ch == "]":
            brackets -= 1

    # If we stopped inside a string, close it.
    if in_str:
        s += '"'
    # Drop trailing comma (JSON doesn't allow it).
    s = re.sub(r",\s*$", "", s)
    # Drop a dangling "key": with no value.
    s = re.sub(r',\s*"[^"]*"\s*:\s*$', "", s)
    s = re.sub(r'"[^"]*"\s*:\s*$', "", s)
    # Close open arrays then objects.
    s += "]" * max(0, brackets)
    s += "}" * max(0, braces)
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


__all__ = ["StreamAssembler", "ToolCallPartial"]
