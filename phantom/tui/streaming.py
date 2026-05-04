"""Streaming response renderer.

The agent loop emits tokens. We accumulate them, render incrementally,
and flush via Rich's Live. Tests inspect the buffer + the rendered
markup-stripped text without instantiating a Live (which would need a
TTY).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

__all__ = ["StreamingResponse", "render_token"]


def render_token(token: str) -> str:
    """Apply Rich-style escaping to a token before it joins the buffer.

    The token may contain bracketed sequences like ``[bold]hello[/bold]``
    coming from the model's tool-formatting pass. We pass them through;
    the only escaping we do is on stray ``[`` characters that aren't
    valid Rich markup, which would otherwise break rendering.
    """
    # Conservative: escape any `[` not followed by a known markup style.
    # Rich's documented styles begin with a letter or `/`.
    out_parts: list[str] = []
    i = 0
    while i < len(token):
        ch = token[i]
        if ch == "[":
            tail = token[i + 1: i + 32]
            if tail and (tail[0] == "/" or tail[0].isalpha()):
                out_parts.append(ch)
            else:
                out_parts.append("\\[")
        else:
            out_parts.append(ch)
        i += 1
    return "".join(out_parts)


@dataclass
class StreamingResponse:
    """Accumulator + flusher for streamed model tokens.

    Usage::

        sr = StreamingResponse()
        for tok in model_stream():
            sr.feed(tok)
        sr.finalize()
        text = sr.rendered_text()
    """

    _buf: list[str] = field(default_factory=list)
    _finalized: bool = False
    flushes: int = 0
    _last_flush_len: int = 0

    def feed(self, token: str) -> None:
        if self._finalized:
            raise RuntimeError("StreamingResponse already finalized")
        self._buf.append(render_token(token))

    def feed_many(self, tokens: Iterable[str]) -> None:
        for t in tokens:
            self.feed(t)

    def finalize(self) -> None:
        self._finalized = True
        self.flushes += 1

    def is_finalized(self) -> bool:
        return self._finalized

    def rendered_text(self) -> str:
        return "".join(self._buf)

    def has_new_content(self) -> bool:
        """Return True if the buffer grew since the last :meth:`mark_flushed`."""
        return len(self.rendered_text()) > self._last_flush_len

    def mark_flushed(self) -> None:
        """Called by the renderer after it pushes the latest text to the screen."""
        self._last_flush_len = len(self.rendered_text())
        self.flushes += 1


# ─── live-renderer adapter (optional rich dep) ──────────────────────────────


def stream_to_live(stream: StreamingResponse, console=None, refresh_per_second: int = 12):
    """Render `stream` into a Rich Live panel until finalized.

    Returns the live context manager. Caller is responsible for feeding
    the stream from another thread or coroutine. This is a thin adapter;
    everything testable lives in :class:`StreamingResponse` itself.
    """
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel

    console = console or Console()

    def _renderable():
        return Panel(stream.rendered_text(), title="phantom", border_style="cyan")

    live = Live(_renderable(), console=console, refresh_per_second=refresh_per_second, transient=False)
    return live
