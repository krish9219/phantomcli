"""Stub STT/TTS engines for tests and CI.

Pure-Python, no audio deps. ``StubSTT`` accumulates frames and returns
"<frames N>" on finalize; ``StubTTS`` returns deterministic PCM bytes
sized 320 bytes per spoken character.
"""

from __future__ import annotations

from phantom.voice.loop import STTEngine, TTSEngine, VoiceFrame, VoiceTurn

__all__ = ["StubSTT", "StubTTS"]


class StubSTT(STTEngine):
    def __init__(self) -> None:
        self._frames = 0
        self._transcript_override: str | None = None

    def feed(self, frame: VoiceFrame) -> None:
        self._frames += 1

    def set_next_transcript(self, text: str) -> None:
        self._transcript_override = text

    def finalize(self) -> str:
        if self._transcript_override is not None:
            t = self._transcript_override
            self._transcript_override = None
            self._frames = 0
            return t
        if self._frames == 0:
            return ""
        out = f"<{self._frames} frames>"
        self._frames = 0
        return out

    def reset(self) -> None:
        self._frames = 0


class StubTTS(TTSEngine):
    def render(self, turn: VoiceTurn) -> bytes:
        return b"\x00\x00" * (320 * max(1, len(turn.text)))
