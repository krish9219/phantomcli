"""Canvas node — JSON-serialisable UI tree.

Each node has a ``kind`` (text / code / table / chart / button / form)
and a free-form ``props`` dict whose schema depends on ``kind``. The
contract is documented per kind in this module's docstrings; the
dashboard renderer consumes it directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phantom.errors import PhantomError

__all__ = ["CanvasNode", "render_to_dict"]


_VALID_KINDS = {"text", "code", "table", "chart", "button", "form", "container"}


@dataclass(frozen=True, slots=True)
class CanvasNode:
    kind: str
    props: dict[str, Any] = field(default_factory=dict)
    children: tuple["CanvasNode", ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise PhantomError(
                f"unknown canvas kind {self.kind!r}; "
                f"allowed: {sorted(_VALID_KINDS)}"
            )
        # Per-kind validation: just enough to catch typos in agent
        # prompt → renderer crashes.
        if self.kind == "text" and "value" not in self.props:
            raise PhantomError("text node requires props.value")
        if self.kind == "code":
            if "value" not in self.props:
                raise PhantomError("code node requires props.value")
            if "language" in self.props and not isinstance(self.props["language"], str):
                raise PhantomError("code.language must be a string")
        if self.kind == "table":
            for k in ("columns", "rows"):
                if k not in self.props:
                    raise PhantomError(f"table node requires props.{k}")
            if not isinstance(self.props["columns"], list):
                raise PhantomError("table.columns must be a list")
            if not isinstance(self.props["rows"], list):
                raise PhantomError("table.rows must be a list")
        if self.kind == "button" and "label" not in self.props:
            raise PhantomError("button node requires props.label")
        if self.kind == "form":
            if "fields" not in self.props:
                raise PhantomError("form node requires props.fields")
            if not isinstance(self.props["fields"], list):
                raise PhantomError("form.fields must be a list")


def render_to_dict(node: CanvasNode) -> dict[str, Any]:
    """Serialise *node* to a JSON-friendly dict."""
    return {
        "kind": node.kind,
        "props": dict(node.props),
        "children": [render_to_dict(c) for c in node.children],
    }
