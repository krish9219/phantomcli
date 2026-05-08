"""``phantom dictate`` — Typer subcommand."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from phantom.voice.dictate import DictateBackendError, dictate

__all__ = ["dictate_cmd"]


def dictate_cmd(
    seconds: float = typer.Option(5.0, "--seconds", "-s", help="recording length"),
    backend: Optional[str] = typer.Option(None, "--backend", help="openai-whisper | stub"),
    audio: Optional[str] = typer.Option(None, "--audio", help="skip recording, transcribe this WAV"),
    raw: bool = typer.Option(False, "--raw", help="print only the transcript"),
) -> None:
    """Record audio and print the transcript."""
    from phantom.licensing import require_pro
    require_pro("dictate")
    try:
        result = dictate(
            seconds=seconds,
            backend=backend,
            audio_path=Path(audio) if audio else None,
        )
    except DictateBackendError as e:
        typer.echo(f"dictate failed: {e}", err=True)
        raise typer.Exit(1)
    if raw:
        typer.echo(result.text)
        return
    typer.echo("")
    typer.echo(f"  backend:    {result.backend}")
    typer.echo(f"  duration:   {result.duration_s:.1f}s")
    typer.echo(f"  transcript: {result.text}")
    typer.echo("")
