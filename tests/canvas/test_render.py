"""Tests for :func:`phantom.canvas.render_to_html`."""

from __future__ import annotations

import json

import pytest

from phantom.canvas import CanvasNode, render_to_html
from phantom.errors import PhantomError


class TestText:
    def test_simple(self):
        n = CanvasNode(kind="text", props={"value": "hello"})
        assert render_to_html(n) == '<p class="phc-text">hello</p>'

    def test_html_escaped(self):
        n = CanvasNode(kind="text", props={"value": "<script>alert(1)</script>"})
        out = render_to_html(n)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_ampersand_escaped(self):
        n = CanvasNode(kind="text", props={"value": "a & b"})
        assert "&amp;" in render_to_html(n)


class TestCode:
    def test_with_language(self):
        n = CanvasNode(kind="code", props={"value": "print(1)", "language": "python"})
        out = render_to_html(n)
        assert 'class="phc-code language-python"' in out
        assert "print(1)" in out

    def test_without_language(self):
        n = CanvasNode(kind="code", props={"value": "x"})
        out = render_to_html(n)
        assert 'class="phc-code"' in out
        assert "language-" not in out

    def test_html_inside_code_escaped(self):
        n = CanvasNode(kind="code", props={"value": "<b>bold</b>", "language": "html"})
        out = render_to_html(n)
        assert "<b>" not in out  # no live tag
        assert "&lt;b&gt;" in out


class TestTable:
    def test_basic(self):
        n = CanvasNode(kind="table", props={
            "columns": ["a", "b"],
            "rows": [[1, 2], [3, 4]],
        })
        out = render_to_html(n)
        assert "<thead>" in out and "<tbody>" in out
        assert "<th>a</th><th>b</th>" in out
        assert "<td>1</td><td>2</td>" in out

    def test_table_html_escaped(self):
        n = CanvasNode(kind="table", props={
            "columns": ["<col>"],
            "rows": [["<row>"]],
        })
        out = render_to_html(n)
        assert "<col>" not in out.replace("<th>", "").replace("</th>", "") \
            .replace("<td>", "").replace("</td>", "")
        assert "&lt;col&gt;" in out


class TestChart:
    def test_emits_data_attr(self):
        n = CanvasNode(kind="chart", props={
            "type": "line",
            "series": [[1, 2, 3]],
        })
        out = render_to_html(n)
        assert 'data-chart=' in out
        # The payload is JSON of the props.
        start = out.index("data-chart='") + len("data-chart='")
        end = out.index("'", start)
        payload = json.loads(out[start:end].replace("&quot;", '"'))
        assert payload["type"] == "line"


class TestButton:
    def test_with_action(self):
        n = CanvasNode(kind="button", props={"label": "Go", "action": "submit_query"})
        out = render_to_html(n)
        assert ">Go</button>" in out
        assert 'data-action="submit_query"' in out


class TestForm:
    def test_renders_fields(self):
        n = CanvasNode(kind="form", props={
            "fields": [
                {"name": "email", "label": "Email", "type": "email"},
                {"name": "msg", "label": "Message", "type": "text"},
            ],
            "submit_label": "Send",
        })
        out = render_to_html(n)
        assert 'name="email"' in out
        assert 'name="msg"' in out
        assert "Send" in out
        # Submit button rendered.
        assert 'type="submit"' in out


class TestContainer:
    def test_recursive_children(self):
        root = CanvasNode(kind="container", props={}, children=(
            CanvasNode(kind="text", props={"value": "alpha"}),
            CanvasNode(kind="text", props={"value": "beta"}),
        ))
        out = render_to_html(root)
        assert out.startswith('<div class="phc-container">')
        assert ">alpha</p>" in out
        assert ">beta</p>" in out
        assert out.endswith("</div>")


class TestErrorPaths:
    def test_excessive_nesting_raises(self):
        # Build a 33-deep container to trip the depth guard.
        node = CanvasNode(kind="text", props={"value": "leaf"})
        for _ in range(40):
            node = CanvasNode(kind="container", props={}, children=(node,))
        with pytest.raises(PhantomError, match="nesting depth"):
            render_to_html(node)
