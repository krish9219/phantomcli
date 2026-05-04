"""
Tool argument schemas + validator.

The engine's tool dispatcher (`_execute_tool`) historically accepted raw
dict args from the model with zero validation. A missing or mistyped field
would silently call the underlying function with an empty string, producing
confusing downstream errors (bash got "", write_file created an empty file
at cwd, etc.).

This module defines a JSON Schema for every built-in tool and exposes
`validate(name, args)` which returns (ok, error_message). The engine calls
this before dispatch and, on failure, returns the schema error to the model
as a tool result so the model can retry with correct args.

Keep schemas deliberately permissive:
- `additionalProperties: true` so model variants emitting extra keys don't
  fail — we just ignore what we don't use
- Required fields enforced strictly
- Types use `anyOf` where the model sometimes sends stringified numbers
"""
from __future__ import annotations

from typing import Any

try:
    from jsonschema import Draft202012Validator
    _JSONSCHEMA_OK = True
except ImportError:  # jsonschema is a hard dep, but be defensive
    _JSONSCHEMA_OK = False


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "run_bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": 8000},
        },
        "required": ["command"],
        "additionalProperties": True,
    },
    "browse_url": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "minLength": 7,
                "maxLength": 2048,
                "pattern": "^https?://",
            },
        },
        "required": ["url"],
        "additionalProperties": True,
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query":       {"type": "string", "minLength": 1, "maxLength": 500},
            "max_results": {
                "anyOf": [
                    {"type": "integer", "minimum": 1, "maximum": 50},
                    {"type": "string", "pattern": "^[0-9]+$"},
                ]
            },
        },
        "required": ["query"],
        "additionalProperties": True,
    },
    "write_file": {
        "type": "object",
        "properties": {
            "path":    {"type": "string", "minLength": 1, "maxLength": 4096},
            "content": {"type": "string", "maxLength": 2_000_000},
        },
        "required": ["path", "content"],
        "additionalProperties": True,
    },
    "read_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 4096},
        },
        "required": ["path"],
        "additionalProperties": True,
    },
    "edit_file": {
        "type": "object",
        "properties": {
            "path":     {"type": "string", "minLength": 1, "maxLength": 4096},
            "old_text": {"type": "string", "minLength": 1},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": True,
    },
    "plan_tasks": {
        "type": "object",
        "properties": {
            "tasks": {
                "anyOf": [
                    {"type": "array", "items": {"type": "string"}, "maxItems": 40},
                    {"type": "string", "maxLength": 8000},
                ]
            },
            "steps": {
                "anyOf": [
                    {"type": "array", "items": {"type": "string"}, "maxItems": 40},
                    {"type": "string", "maxLength": 8000},
                ]
            },
        },
        "additionalProperties": True,
    },
}


def _format_errors(errors: list) -> str:
    """Render jsonschema errors into a terse model-readable message."""
    lines = []
    for e in errors[:4]:
        path = ".".join(str(p) for p in e.absolute_path) or "(root)"
        lines.append(f"  • {path}: {e.message}")
    if len(errors) > 4:
        lines.append(f"  • … and {len(errors) - 4} more")
    return "\n".join(lines)


def validate(name: str, args: Any) -> tuple[bool, str]:
    """Validate `args` against the schema for tool `name`.

    Returns (ok, error_message). On ok=False, error_message is a
    structured string designed to be fed back to the model so it can
    retry with corrected args.
    """
    if name not in TOOL_SCHEMAS:
        # Unknown tool — dispatcher will handle; no schema to validate against.
        return True, ""
    if not isinstance(args, dict):
        return False, (
            f"INVALID_TOOL_ARGS({name}): args must be a JSON object, "
            f"got {type(args).__name__}"
        )
    if not _JSONSCHEMA_OK:
        # Graceful degrade — still enforce 'required' ourselves so missing
        # keys don't silently become empty strings.
        schema = TOOL_SCHEMAS[name]
        missing = [k for k in schema.get("required", []) if k not in args]
        if missing:
            return False, (
                f"INVALID_TOOL_ARGS({name}): missing required keys: "
                f"{', '.join(missing)}"
            )
        return True, ""
    validator = Draft202012Validator(TOOL_SCHEMAS[name])
    errors = sorted(validator.iter_errors(args), key=lambda e: e.path)
    if not errors:
        return True, ""
    return False, (
        f"INVALID_TOOL_ARGS({name}):\n{_format_errors(errors)}\n"
        f"Retry the call with corrected arguments."
    )


__all__ = ["validate", "TOOL_SCHEMAS"]
