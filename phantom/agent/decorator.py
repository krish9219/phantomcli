"""``@tool`` decorator — ergonomic tool registration.

The Stage-4 :class:`phantom.agent.session.ToolDefinition` works fine
but requires three call sites per tool (name, schema, handler). Block
C of the v4.1 expansion adds a lighter spelling::

    from phantom.agent.decorator import tool

    @tool(
        name="echo",
        description="Echo a string back.",
        schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    def echo(args: dict) -> str:
        return args["text"]

The decorator returns a fully-formed :class:`ToolDefinition`. The
underlying function stays callable, so unit tests can call ``echo({...})``
directly without going through the agent loop.

Backwards-compatible: existing ``ToolDefinition(...)`` construction
continues to work. The decorator is sugar.
"""

from __future__ import annotations

from typing import Any, Callable

from phantom.agent.session import ToolDefinition
from phantom.errors import PhantomError

__all__ = ["tool"]


# A handler returns a JSON-serialisable string (typically
# ``json.dumps(...)``). Same shape as :data:`phantom.agent.session.ToolHandler`.
ToolHandler = Callable[[dict[str, Any]], str]


def tool(
    *,
    name: str,
    description: str,
    schema: dict[str, Any] | None = None,
) -> Callable[[ToolHandler], ToolDefinition]:
    """Decorate a handler function and return a :class:`ToolDefinition`.

    Parameters
    ----------
    name:
        Tool name as the model sees it. Must be non-empty.
    description:
        One-line description. Shown to the model in the tools list.
    schema:
        JSON-schema dict for the input arguments. If omitted, defaults
        to ``{"type": "object"}`` (any object accepted; the handler is
        responsible for validating).

    Returns
    -------
    A factory that, when applied to a handler function, returns the
    :class:`ToolDefinition` directly. The original function remains
    accessible via the result's ``handler`` attribute.

    Raises
    ------
    phantom.errors.PhantomError
        If *name* is empty or *description* is empty at decoration
        time. We fail at import-time rather than first-call so a
        misconfigured plugin never loads.
    """
    if not name:
        raise PhantomError("@tool requires a non-empty name")
    if not description:
        raise PhantomError("@tool requires a non-empty description")
    final_schema: dict[str, Any] = schema if schema is not None else {"type": "object"}

    def _decorate(fn: ToolHandler) -> ToolDefinition:
        if not callable(fn):
            raise PhantomError(
                f"@tool({name!r}) must decorate a callable, got {type(fn).__name__}"
            )
        return ToolDefinition(
            name=name,
            description=description,
            input_schema=final_schema,
            handler=fn,
        )

    return _decorate
