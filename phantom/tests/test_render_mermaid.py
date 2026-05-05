"""Tests for terminal-side mermaid rendering."""

from __future__ import annotations

import base64
import os
import stat
import sys
from pathlib import Path

import pytest

from phantom.render.mermaid import (
    MermaidRenderer,
    TerminalCapabilities,
    _emit_ascii,
    _emit_kitty,
    detect_terminal_capabilities,
    render_mermaid,
)


# Synthetic 1x1 transparent PNG — small enough to inline.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGAYZcL5wAAAABJRU5ErkJggg=="
)


# ─── capability detection ────────────────────────────────────────────────────


def test_caps_pipe_returns_ascii():
    caps = detect_terminal_capabilities(env={}, is_tty=False)
    assert not caps.kitty and not caps.sixel
    assert caps.best_image_protocol == "ascii"


def test_caps_kitty_via_window_id():
    caps = detect_terminal_capabilities(
        env={"KITTY_WINDOW_ID": "1", "TERM": "xterm-kitty"}, is_tty=True,
    )
    assert caps.kitty
    assert caps.best_image_protocol == "kitty"


def test_caps_kitty_via_term_program():
    caps = detect_terminal_capabilities(
        env={"TERM_PROGRAM": "kitty"}, is_tty=True,
    )
    assert caps.kitty


def test_caps_wezterm_is_sixel():
    caps = detect_terminal_capabilities(
        env={"TERM_PROGRAM": "WezTerm"}, is_tty=True,
    )
    assert caps.sixel
    assert caps.best_image_protocol == "sixel"


def test_caps_phantom_env_force_sixel():
    caps = detect_terminal_capabilities(
        env={"PHANTOM_TERM_SIXEL": "1", "TERM": "screen"}, is_tty=True,
    )
    assert caps.sixel


def test_caps_kitty_implies_truecolor():
    caps = detect_terminal_capabilities(
        env={"KITTY_WINDOW_ID": "1"}, is_tty=True,
    )
    assert caps.truecolor


def test_caps_truecolor_via_colorterm():
    caps = detect_terminal_capabilities(
        env={"COLORTERM": "truecolor"}, is_tty=True,
    )
    assert caps.truecolor


def test_caps_falls_back_to_80x24_when_size_unknown(monkeypatch: pytest.MonkeyPatch):
    def _boom(*a, **kw):
        raise OSError("not a tty")
    monkeypatch.setattr(os, "get_terminal_size", _boom)
    caps = detect_terminal_capabilities(env={"TERM": "xterm"}, is_tty=True)
    assert caps.width_cols == 80
    assert caps.height_rows == 24


# ─── kitty protocol encoding ─────────────────────────────────────────────────


def test_kitty_encoding_starts_with_apc():
    out = _emit_kitty(_TINY_PNG)
    assert out.startswith("\x1b_Ga=T,f=100")
    assert out.endswith("\n")
    assert "\x1b\\" in out  # APC terminator


def test_kitty_encoding_chunks_large_payloads():
    big = b"\x00" * 12_000  # > 4096 base64 chars
    out = _emit_kitty(big)
    # We should see m=1 (more) at least once and m=0 (last chunk) once.
    assert ",m=1;" in out
    assert ",m=0;" in out


def test_kitty_encoding_single_chunk_for_small_payload():
    out = _emit_kitty(_TINY_PNG)
    # Small enough to fit in one base64 chunk.
    assert out.count(",m=") == 1


# ─── ASCII fallback ──────────────────────────────────────────────────────────


def test_ascii_fallback_includes_diagram_text():
    caps = TerminalCapabilities(kitty=False, sixel=False, truecolor=False,
                                width_cols=80, height_rows=24)
    out = _emit_ascii("graph TD\n  A --> B", caps, reason="testing")
    assert "graph TD" in out
    assert "A --> B" in out
    assert "testing" in out
    # box-drawing chars present
    assert "┌" in out and "└" in out


def test_ascii_fallback_respects_width():
    caps = TerminalCapabilities(kitty=False, sixel=False, truecolor=False,
                                width_cols=60, height_rows=24)
    out = _emit_ascii("x" * 200, caps)
    # Body lines should be capped near width
    for line in out.splitlines():
        assert len(line) <= 100


# ─── orchestrator ────────────────────────────────────────────────────────────


def test_renderer_falls_back_to_ascii_when_no_mmdc(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("phantom.render.mermaid._mmdc_path", lambda: None)
    caps = TerminalCapabilities(kitty=True, sixel=False, truecolor=True,
                                width_cols=120, height_rows=40)
    r = MermaidRenderer(caps)
    out = r.render("graph TD\n  X --> Y")
    assert "X --> Y" in out
    assert "no mmdc" in out


@pytest.mark.skipif(sys.platform == "win32", reason="mmdc shim is a node script — not directly executable on Windows runners")
def test_renderer_uses_kitty_when_mmdc_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Inject a fake mmdc that writes our tiny PNG; assert kitty escape emitted."""
    fake_mmdc = tmp_path / "mmdc"
    fake_mmdc.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, base64, pathlib, argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('-i'); p.add_argument('-o'); p.add_argument('-t', default='dark')\n"
        "p.add_argument('-b', default='transparent'); p.add_argument('-q', action='store_true')\n"
        "a = p.parse_args()\n"
        "png = base64.b64decode('"
        + base64.b64encode(_TINY_PNG).decode("ascii")
        + "')\n"
        "pathlib.Path(a.o).write_bytes(png)\n"
    )
    fake_mmdc.chmod(fake_mmdc.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PHANTOM_MMDC", str(fake_mmdc))
    monkeypatch.setattr("phantom.render.mermaid._mmdc_path", lambda: str(fake_mmdc))

    caps = TerminalCapabilities(kitty=True, sixel=False, truecolor=True,
                                width_cols=120, height_rows=40)
    r = MermaidRenderer(caps)
    out = r.render("graph TD\n  A --> B")
    assert out.startswith("\x1b_Ga=T,f=100")


@pytest.mark.skipif(sys.platform == "win32", reason="mmdc shim is a node script — not directly executable on Windows runners")
def test_renderer_falls_back_to_ascii_when_mmdc_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_mmdc = tmp_path / "mmdc"
    fake_mmdc.write_text("#!/bin/sh\nexit 1\n")
    fake_mmdc.chmod(fake_mmdc.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr("phantom.render.mermaid._mmdc_path", lambda: str(fake_mmdc))

    caps = TerminalCapabilities(kitty=True, sixel=False, truecolor=True,
                                width_cols=120, height_rows=40)
    r = MermaidRenderer(caps)
    out = r.render("graph TD\n  A --> B")
    # Should fall back, not raise.
    assert "A --> B" in out
    assert "mmdc failed" in out


def test_render_mermaid_one_shot_does_not_raise():
    out = render_mermaid("graph TD\n  X --> Y")
    assert isinstance(out, str)
    assert len(out) > 0
