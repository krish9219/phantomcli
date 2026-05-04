"""Server-side HTML renderer for canvas trees.

A :class:`CanvasNode` tree converts to an HTML fragment that the
dashboard can drop into the DOM as-is. The renderer is conservative:
every textual value is HTML-escaped, every attribute value is quoted,
and unknown ``kind`` values raise rather than silently skip.

The output is **a fragment** (no ``<html>``/``<body>`` wrapper). The
dashboard's app shell controls page-level structure.

Example
-------

>>> from phantom.canvas import CanvasNode
>>> from phantom.canvas.render import render_to_html
>>> node = CanvasNode(kind="text", props={"value": "Hello & welcome"})
>>> render_to_html(node)
'<p class="phc-text">Hello &amp; welcome</p>'
"""

from __future__ import annotations

import html
from typing import Any

from phantom.canvas.node import CanvasNode
from phantom.errors import PhantomError

__all__ = ["render_to_html"]


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _attrs(extra: dict[str, str]) -> str:
    parts = [f'{k}="{_escape(v)}"' for k, v in extra.items()]
    return " " + " ".join(parts) if parts else ""


def render_to_html(node: CanvasNode, *, depth: int = 0) -> str:
    """Render *node* to an HTML fragment."""
    if depth > 32:
        # Defence against runaway nesting / cycles.
        raise PhantomError("canvas render: nesting depth exceeded 32")

    kind = node.kind
    children_html = "".join(
        render_to_html(c, depth=depth + 1) for c in node.children
    )

    if kind == "container":
        return f'<div class="phc-container">{children_html}</div>'

    if kind == "text":
        return f'<p class="phc-text">{_escape(node.props["value"])}</p>'

    if kind == "code":
        lang = _escape(node.props.get("language", ""))
        body = _escape(node.props["value"])
        cls = "phc-code"
        if lang:
            cls += f" language-{lang}"
        return f'<pre class="{cls}"><code>{body}</code></pre>'

    if kind == "table":
        cols = node.props["columns"]
        rows = node.props["rows"]
        thead = "".join(f"<th>{_escape(c)}</th>" for c in cols)
        body_rows: list[str] = []
        for row in rows:
            tds = "".join(f"<td>{_escape(v)}</td>" for v in row)
            body_rows.append(f"<tr>{tds}</tr>")
        return (
            '<table class="phc-table">'
            f"<thead><tr>{thead}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table>"
        )

    if kind == "chart":
        # Charts are client-rendered (SVG); we emit a placeholder div
        # carrying the JSON payload as a data attribute.
        import json as _json
        payload = _escape(_json.dumps(node.props, separators=(",", ":")))
        return (
            f'<div class="phc-chart" data-chart=\'{payload}\'>chart</div>'
        )

    if kind == "button":
        label = _escape(node.props["label"])
        action = _escape(node.props.get("action", ""))
        return (
            f'<button class="phc-button" data-action="{action}">{label}</button>'
        )

    if kind == "form":
        fields = node.props["fields"]
        inputs: list[str] = []
        for f in fields:
            if not isinstance(f, dict):
                continue
            name = _escape(f.get("name", ""))
            kind_in = _escape(f.get("type", "text"))
            label = _escape(f.get("label", name))
            inputs.append(
                f'<label class="phc-field">{label}'
                f'<input type="{kind_in}" name="{name}"></label>'
            )
        submit = _escape(node.props.get("submit_label", "Submit"))
        return (
            '<form class="phc-form">'
            + "".join(inputs)
            + f'<button type="submit">{submit}</button>'
            "</form>"
        )

    raise PhantomError(f"render: unknown canvas kind {kind!r}")
