"""Piper TTS adapter.

Wraps the ``piper-tts`` package's offline neural TTS. Optional dep
under ``phantom-cli[voice]``.

Each :meth:`render` call is one synthesis pass. The result is PCM-16
mono at the model's native sample rate (typically 22 050 Hz; the
voice loop downsamples in a later stage if needed).
"""

from __future__ import annotations

from typing import Any

from phantom.voice.loop import VoiceTurn

__all__ = ["PiperTTS"]


class PiperTTS:
    """TTS engine backed by Piper.

    Parameters
    ----------
    model_path:
        Path to a ``.onnx`` voice model. Required at runtime; tests
        inject a stub model.
    model:
        Pre-instantiated ``PiperVoice`` (tests use this).
    speaker:
        Optional speaker index for multi-speaker models.
    """

    def __init__(
        self,
        *,
        model_path: str | None = None,
        model: Any = None,
        speaker: int | None = None,
    ) -> None:
        if model is None and model_path is None:
            raise ValueError("PiperTTS requires either model_path or model")
        self._model_path = model_path
        self._model = model
        self._speaker = speaker

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        from piper.voice import PiperVoice  # type: ignore[import-not-found]
        assert self._model_path is not None
        self._model = PiperVoice.load(self._model_path)
        return self._model

    # ─── TTSEngine protocol ────────────────────────────────────────────

    def render(self, turn: VoiceTurn) -> bytes:
        if not turn.text.strip():
            return b""
        model = self._ensure_model()
        # Piper's API: synthesize_stream_raw yields chunks of int16 PCM.
        chunks: list[bytes] = []
        for chunk in model.synthesize_stream_raw(
            turn.text,
            speaker_id=self._speaker,
            length_scale=1.0 / max(turn.speed, 0.1),
        ):
            chunks.append(chunk)
        return b"".join(chunks)
