"""Tests for :mod:`phantom.canvas.node`."""

from __future__ import annotations

import pytest

from phantom.canvas import CanvasNode, render_to_dict
from phantom.errors import PhantomError


class TestCanvasNodeValidation:
    def test_text_node(self):
        n = CanvasNode(kind="text", props={"value": "hi"})
        assert n.props["value"] == "hi"

    def test_text_requires_value(self):
        with pytest.raises(PhantomError, match="value"):
            CanvasNode(kind="text", props={})

    def test_unknown_kind_rejected(self):
        with pytest.raises(PhantomError, match="unknown canvas kind"):
            CanvasNode(kind="hologram", props={})

    def test_code_node(self):
        n = CanvasNode(kind="code", props={"value": "x=1", "language": "python"})
        assert n.props["language"] == "python"

    def test_code_requires_value(self):
        with pytest.raises(PhantomError):
            CanvasNode(kind="code", props={"language": "python"})

    def test_code_language_must_be_string(self):
        with pytest.raises(PhantomError, match="language"):
            CanvasNode(kind="code", props={"value": "x", "language": 1})

    def test_table_node(self):
        n = CanvasNode(kind="table", props={
            "columns": ["a", "b"],
            "rows": [[1, 2], [3, 4]],
        })
        assert n.props["columns"] == ["a", "b"]

    def test_table_requires_columns_and_rows(self):
        with pytest.raises(PhantomError):
            CanvasNode(kind="table", props={"rows": []})
        with pytest.raises(PhantomError):
            CanvasNode(kind="table", props={"columns": []})

    def test_button_requires_label(self):
        with pytest.raises(PhantomError, match="label"):
            CanvasNode(kind="button", props={})

    def test_form_requires_fields_list(self):
        with pytest.raises(PhantomError, match="fields"):
            CanvasNode(kind="form", props={})
        with pytest.raises(PhantomError, match="fields"):
            CanvasNode(kind="form", props={"fields": "wrong-type"})


class TestRenderToDict:
    def test_minimal(self):
        n = CanvasNode(kind="text", props={"value": "hi"})
        assert render_to_dict(n) == {
            "kind": "text", "props": {"value": "hi"}, "children": [],
        }

    def test_nested(self):
        root = CanvasNode(
            kind="container",
            props={},
            children=(
                CanvasNode(kind="text", props={"value": "title"}),
                CanvasNode(kind="text", props={"value": "body"}),
            ),
        )
        d = render_to_dict(root)
        assert d["kind"] == "container"
        assert len(d["children"]) == 2
        assert d["children"][0]["props"]["value"] == "title"
