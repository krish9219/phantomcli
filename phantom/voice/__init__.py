"""Phantom realtime voice — STT + TTS pipeline.

Stage 6 ships the **pipeline framework**. Real engines (faster-whisper
for STT, Piper for TTS) are optional extras (`pip install
phantom-cli[voice]`) and are loaded lazily; the pipeline framework
itself runs without them.

Design:

* :class:`VoiceFrame`  — one chunk of PCM-16 audio with a timestamp.
* :class:`STTEngine`   — protocol; concrete engines plug in.
* :class:`TTSEngine`   — protocol; concrete engines plug in.
* :class:`VoiceLoop`   — orchestrates frame-by-frame STT + barge-in +
  TTS playback queue.

The framework is fully unit-testable with stub engines; production
deployments wire in `FasterWhisperSTT` / `PiperTTS` (lands in Stage 8
as an optional extra).
"""

from __future__ import annotations

from phantom.voice.loop import (
    STTEngine,
    TTSEngine,
    VoiceFrame,
    VoiceLoop,
    VoiceTurn,
)

__all__ = [
    "STTEngine",
    "TTSEngine",
    "VoiceFrame",
    "VoiceLoop",
    "VoiceTurn",
]
