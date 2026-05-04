"""Verifies the dashboard ships the mermaid renderer + script tag."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"


def test_index_html_includes_mermaid_script():
    html = (STATIC / "index.html").read_text()
    assert "mermaid.min.js" in html
    assert "mermaid_render.js" in html
    assert "securityLevel:'strict'" in html


def test_mermaid_render_js_is_present_and_strict():
    js = (STATIC / "mermaid_render.js").read_text()
    # We must not eval untrusted input.
    assert "eval(" not in js
    # MutationObserver wires the renderer to dynamically-injected content.
    assert "MutationObserver" in js
    # Renderer keys off ```mermaid fences only.
    assert "language-mermaid" in js


def test_mermaid_script_uses_subresource_integrity():
    html = (STATIC / "index.html").read_text()
    assert 'integrity="sha384-' in html
    assert 'crossorigin="anonymous"' in html
