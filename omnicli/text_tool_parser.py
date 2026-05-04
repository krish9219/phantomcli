"""
Text-embedded tool-call parser — extracted from engine.py.

Some models (GLM, Llama variants) emit tool calls as XML text in the
assistant response body rather than using the structured tool-use API.
This module parses all three known text formats and returns a uniform
`[{"name", "args"}]` list that the dispatch layer consumes.

Formats handled:
  1. GLM arg-key/arg-value XML:
       <tool_call>run_bash
       <arg_key>command</arg_key><arg_value>ls</arg_value>
       </tool_call>
  2. JSON-inside-XML:
       <tool_call>{"name": "run_bash", "arguments": {"command": "ls"}}</tool_call>
  3. function(json):
       <tool_call>run_bash({"command": "ls"})</tool_call>

All parsers are lenient: a single malformed tool_call block doesn't
prevent others from being recognised.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Tools allowed in the "format 1" name-on-first-line path. Kept in sync
# with tool_schemas.TOOL_SCHEMAS.
_KNOWN_TOOL_NAMES = {
    "run_bash", "browse_url", "web_search",
    "write_file", "edit_file", "read_file",
    "plan_tasks",
}

_TOOL_CALL_BLOCK_RE = re.compile(
    r'<tool_call>(.*?)</tool_call>', re.DOTALL | re.IGNORECASE,
)
_ARG_KEY_RE = re.compile(r'<arg_key>(.*?)</arg_key>', re.DOTALL)
_ARG_VAL_RE = re.compile(r'<arg_value>(.*?)</arg_value>', re.DOTALL)
_FUNC_CALL_RE = re.compile(r'(\w+)\s*\((\{.*\})\)', re.DOTALL)


def parse_text_tool_calls(text: str) -> list[dict]:
    """Return every parseable tool call in `text` as a list of
    {"name", "args"} dicts. Unrecognised / malformed blocks are skipped."""
    if not text:
        return []
    out: list[dict] = []
    for m in _TOOL_CALL_BLOCK_RE.finditer(text):
        body = m.group(1).strip()

        # Format 2: JSON object
        try:
            obj = json.loads(body)
            if isinstance(obj, dict) and "name" in obj:
                args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
                out.append({"name": obj["name"], "args": args})
                continue
        except (json.JSONDecodeError, ValueError):
            pass

        # Format 1: first line = function name, then arg_key/arg_value pairs
        lines = body.splitlines()
        first = lines[0].strip().rstrip("(") if lines else ""
        if first in _KNOWN_TOOL_NAMES:
            args: dict = {}
            keys = _ARG_KEY_RE.findall(body)
            vals = _ARG_VAL_RE.findall(body)
            for k, v in zip(keys, vals):
                args[k.strip()] = v.strip()
            out.append({"name": first, "args": args})
            continue

        # Format 3: funcname(json)
        m3 = _FUNC_CALL_RE.match(body)
        if m3:
            try:
                out.append({"name": m3.group(1), "args": json.loads(m3.group(2))})
            except json.JSONDecodeError:
                pass

    return out


def strip_tool_calls(text: str) -> str:
    """Return `text` with every `<tool_call>...</tool_call>` block removed."""
    return re.sub(
        r'<tool_call>.*?</tool_call>', '',
        text or '',
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()


__all__ = ["parse_text_tool_calls", "strip_tool_calls"]
