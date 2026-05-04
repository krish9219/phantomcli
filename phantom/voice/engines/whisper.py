"""faster-whisper STT adapter.

Wraps :mod:`faster_whisper`'s ``WhisperModel``. Optional dependency:
``pip install phantom-cli[voice]`` brings it in.

The adapter buffers PCM frames into one bytes blob, then on
:meth:`finalize` invokes the model and returns the joined transcript.
For sub-utterance streaming, swap to a model with VAD-based incremental
decoding; the public Engine protocol stays the same.
"""

from __future__ import annotations

from typing import Any

from phantom.voice.loop import VoiceFrame

__all__ = ["FasterWhisperSTT"]


class FasterWhisperSTT:
    """STT engine backed by faster-whisper.

    Parameters
    ----------
    model_size:
        ``"tiny"`` / ``"base"`` / ``"small"`` / ``"medium"`` / ``"large-v3"``.
        The default ``"small"`` runs on CPU.
    language:
        ISO-639-1 code; auto-detect when None.
    model:
        Pre-instantiated ``WhisperModel`` (tests inject a stub here so
        the import-time cost of the real model is avoided).
    """

    def __init__(
        self,
        *,
        model_size: str = "small",
        language: str | None = None,
        model: Any = None,
    ) -> None:
        self._model_size = model_size
        self._language = language
        self._buffer = bytearray()
        self._sample_rate = 16000
        self._model = model  # lazy-loaded on first finalize()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        self._model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
        return self._model

    # ─── STTEngine protocol ────────────────────────────────────────────

    def feed(self, frame: VoiceFrame) -> None:
        if frame.sample_rate != self._sample_rate:
            raise ValueError(
                f"FasterWhisperSTT expects {self._sample_rate} Hz, "
                f"got {frame.sample_rate}"
            )
        self._buffer.extend(frame.pcm)

    def finalize(self) -> str:
        if not self._buffer:
            return ""
        # faster-whisper accepts numpy arrays (float32, mono) or paths.
        # We convert PCM-16 bytes to float32 in [-1, 1].
        import numpy as np  # type: ignore[import-not-found]
        pcm_int16 = np.frombuffer(bytes(self._buffer), dtype=np.int16)
        audio = pcm_int16.astype(np.float32) / 32768.0
        model = self._ensure_model()
        segments, _ = model.transcribe(
            audio,
            language=self._language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def reset(self) -> None:
        self._buffer.clear()
