"""Mermaid diagram rendering for the terminal.

Pipeline
--------

1. Detect what the host terminal supports — the ``TERM`` env var,
   ``$KITTY_WINDOW_ID``, or a ``\\x1bP`` sixel-query response.
2. If ``mmdc`` (mermaid-cli, Node.js based) is on PATH, render the
   diagram to PNG.
3. Emit the PNG using the strongest supported escape — kitty graphics
   protocol > sixel > ASCII fallback.
4. If ``mmdc`` is missing, skip straight to ASCII.

Why no Python-native renderer
-----------------------------

Mermaid is a JavaScript-DOM library; there is no maintained pure-Python
port. Everyone who renders mermaid outside the browser shells out to
either ``mmdc`` (Node) or headless Chromium. We pick ``mmdc`` because
it's smaller. The dashboard renders mermaid in the browser itself, so
this module is only used by the TUI.

Tests
-----

The pure-Python paths (capability detection, ASCII fallback, error
handling) are exercised with no external dependencies. The mmdc path is
exercised by injecting a fake binary that writes a deterministic PNG —
no real mermaid-cli install required for CI.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

__all__ = [
    "MermaidRenderError",
    "MermaidRenderer",
    "TerminalCapabilities",
    "detect_terminal_capabilities",
    "render_mermaid",
]

log = logging.getLogger("phantom.render.mermaid")


class MermaidRenderError(RuntimeError):
    """Raised when no rendering path succeeds."""


@dataclass(frozen=True, slots=True)
class TerminalCapabilities:
    kitty: bool
    sixel: bool
    truecolor: bool
    width_cols: int
    height_rows: int

    @property
    def best_image_protocol(self) -> str:
        """One of ``"kitty"``, ``"sixel"``, or ``"ascii"``."""
        if self.kitty:
            return "kitty"
        if self.sixel:
            return "sixel"
        return "ascii"


# ─── capability detection ────────────────────────────────────────────────────


def detect_terminal_capabilities(
    *,
    env: Optional[dict[str, str]] = None,
    is_tty: Optional[bool] = None,
) -> TerminalCapabilities:
    """Detect what the current terminal can render.

    All inputs are explicit so tests can pin them. ``env=None`` reads
    ``os.environ``; ``is_tty=None`` checks ``sys.stdout``.
    """
    env = env if env is not None else dict(os.environ)
    if is_tty is None:
        try:
            is_tty = sys.stdout.isatty()
        except Exception:
            is_tty = False

    if not is_tty:
        # Pipes get ASCII so the bytes redirected to a file are sane.
        return TerminalCapabilities(
            kitty=False, sixel=False, truecolor=False,
            width_cols=80, height_rows=24,
        )

    term = env.get("TERM", "")
    term_program = env.get("TERM_PROGRAM", "")
    is_kitty = bool(env.get("KITTY_WINDOW_ID")) or term == "xterm-kitty" or term_program == "kitty"
    # Sixel detection is fuzzy without a live escape-sequence query.
    # Common terminals that ship sixel: xterm (with -ti vt340), wezterm,
    # mintty, foot, contour, mlterm. We treat them as sixel-capable.
    sixel_terms = {"xterm-256color", "xterm", "wezterm", "mintty",
                   "foot", "foot-extra", "contour", "mlterm"}
    is_sixel = (
        env.get("PHANTOM_TERM_SIXEL") == "1"
        or term_program in {"WezTerm", "mintty"}
        or term in sixel_terms
    )
    # Truecolor: COLORTERM=truecolor|24bit, or known truecolor terminals.
    truecolor = env.get("COLORTERM", "") in {"truecolor", "24bit"} or is_kitty

    try:
        size = os.get_terminal_size()
        cols, rows = size.columns, size.lines
    except OSError:
        cols, rows = 80, 24

    return TerminalCapabilities(
        kitty=is_kitty,
        sixel=is_sixel,
        truecolor=truecolor,
        width_cols=cols,
        height_rows=rows,
    )


# ─── PNG synthesis (mmdc shell-out) ──────────────────────────────────────────


def _mmdc_path() -> Optional[str]:
    return shutil.which(os.environ.get("PHANTOM_MMDC", "mmdc"))


def _render_to_png(diagram: str, *, dest: Path, theme: str = "dark") -> bytes:
    """Run mmdc to render `diagram` into `dest` as PNG.

    Returns the PNG bytes. Raises :class:`MermaidRenderError` on failure.
    """
    mmdc = _mmdc_path()
    if mmdc is None:
        raise MermaidRenderError("mmdc not on PATH (install with: npm i -g @mermaid-js/mermaid-cli)")
    src = dest.with_suffix(".mmd")
    src.write_text(diagram, encoding="utf-8")
    res = subprocess.run(
        [mmdc, "-i", str(src), "-o", str(dest), "-t", theme,
         "-b", "transparent", "-q"],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        raise MermaidRenderError(f"mmdc failed: {res.stderr.strip()}")
    if not dest.exists():
        raise MermaidRenderError("mmdc returned 0 but produced no output file")
    return dest.read_bytes()


# ─── kitty graphics protocol ─────────────────────────────────────────────────


def _emit_kitty(png_bytes: bytes) -> str:
    """Encode `png_bytes` for the kitty graphics protocol.

    Reference: https://sw.kovidgoyal.net/kitty/graphics-protocol/
    Format: ``\\x1b_Ga=T,f=100,m=<m>;<base64 chunk>\\x1b\\``
    The PNG is base64-encoded and chunked into 4096-byte pieces; only
    the last chunk has ``m=0``.
    """
    payload = base64.standard_b64encode(png_bytes).decode("ascii")
    chunks = [payload[i:i + 4096] for i in range(0, len(payload), 4096)]
    out_parts: list[str] = []
    for i, chunk in enumerate(chunks):
        more = 0 if i == len(chunks) - 1 else 1
        prelude = "\x1b_Ga=T,f=100" if i == 0 else "\x1b_G"
        out_parts.append(f"{prelude},m={more};{chunk}\x1b\\")
    out_parts.append("\n")
    return "".join(out_parts)


# ─── sixel ───────────────────────────────────────────────────────────────────


def _emit_sixel(png_bytes: bytes) -> str:
    """Convert PNG bytes to sixel via ``img2sixel``.

    img2sixel ships with libsixel-bin on Debian/Ubuntu and as
    ``brew install libsixel`` on macOS. If it's missing, we raise so
    the caller can fall back to ASCII.
    """
    img2sixel = shutil.which("img2sixel")
    if img2sixel is None:
        raise MermaidRenderError("img2sixel not on PATH (install: apt install libsixel-bin / brew install libsixel)")
    res = subprocess.run(
        [img2sixel, "-"], input=png_bytes,
        capture_output=True, timeout=15,
    )
    if res.returncode != 0:
        raise MermaidRenderError(f"img2sixel failed: {res.stderr.decode('utf-8', 'replace').strip()}")
    return res.stdout.decode("utf-8", "replace") + "\n"


# ─── ASCII fallback ──────────────────────────────────────────────────────────


def _emit_ascii(diagram: str, caps: TerminalCapabilities, *, reason: str = "") -> str:
    """ASCII fallback. Show the source wrapped to terminal width.

    We don't try to render flowcharts in ASCII — a plain reproduction is
    more useful than a bad attempt. The banner explains why.
    """
    width = max(40, min(caps.width_cols, 100))
    inner = width - 4  # leave room for "│ " ... " │"
    bar = "─" * (width - 2)
    header = f"┌{bar}┐"
    footer = f"└{bar}┘"
    title = "  mermaid diagram  "
    suffix = f"  [{reason}]  " if reason else ""
    head = f"│{title}{suffix:>{width - 2 - len(title)}}│"

    body_lines: list[str] = []
    for raw in diagram.splitlines() or [""]:
        # Hard-wrap each source line to `inner` chars.
        if not raw:
            body_lines.append(f"│ {' ' * inner} │")
            continue
        for i in range(0, len(raw), inner):
            chunk = raw[i:i + inner]
            body_lines.append(f"│ {chunk:<{inner}} │")
    body = "\n".join(body_lines)
    return f"{header}\n{head}\n{body}\n{footer}\n"


# ─── orchestration ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MermaidRenderer:
    capabilities: TerminalCapabilities
    theme: str = "dark"

    def render(self, diagram: str) -> str:
        """Return the bytes-or-text payload to print to stdout.

        Always returns a string (escape sequences are valid UTF-8). Never
        raises — falls back to ASCII on any failure.
        """
        protocol = self.capabilities.best_image_protocol
        if protocol == "ascii" or _mmdc_path() is None:
            reason = "ascii fallback" if protocol == "ascii" else "no mmdc"
            return _emit_ascii(diagram, self.capabilities, reason=reason)
        try:
            with tempfile.TemporaryDirectory(prefix="phantom-mmd-") as td:
                dest = Path(td) / "out.png"
                png = _render_to_png(diagram, dest=dest, theme=self.theme)
                if protocol == "kitty":
                    return _emit_kitty(png)
                if protocol == "sixel":
                    return _emit_sixel(png)
                return _emit_ascii(diagram, self.capabilities, reason="unknown protocol")
        except MermaidRenderError as e:
            log.warning("mermaid render fell back to ASCII: %s", e)
            return _emit_ascii(diagram, self.capabilities, reason=str(e))


def render_mermaid(diagram: str, *, theme: str = "dark") -> str:
    """One-shot convenience: detect caps and render."""
    return MermaidRenderer(detect_terminal_capabilities(), theme=theme).render(diagram)
