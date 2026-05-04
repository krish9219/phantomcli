"""Voice pipeline core.

The loop is event-driven: caller pushes :class:`VoiceFrame` objects,
the loop accumulates until VAD or a fixed cadence triggers a transcript
flush, then dispatches the transcript to a callback. Outbound speech is
queued as :class:`VoiceTurn` objects which the loop renders to PCM via
the configured TTS engine.

Barge-in: if the user speaks while the agent is mid-utterance, the
loop cancels the current TTS turn and clears the playback queue.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

__all__ = [
    "STTEngine",
    "TTSEngine",
    "VoiceFrame",
    "VoiceLoop",
    "VoiceTurn",
]


@dataclass(frozen=True, slots=True)
class VoiceFrame:
    """One chunk of PCM-16 mono audio at 16 kHz."""

    pcm: bytes
    sample_rate: int = 16000
    timestamp_ms: int = 0


@dataclass(frozen=True, slots=True)
class VoiceTurn:
    """One outbound utterance the agent wants to speak."""

    text: str
    voice: str = "default"
    speed: float = 1.0


@runtime_checkable
class STTEngine(Protocol):
    """Streaming speech-to-text engine."""

    def feed(self, frame: VoiceFrame) -> None: ...
    def finalize(self) -> str: ...
    def reset(self) -> None: ...


@runtime_checkable
class TTSEngine(Protocol):
    """Text-to-speech engine. Returns one batch of PCM bytes per turn."""

    def render(self, turn: VoiceTurn) -> bytes: ...


class VoiceLoop:
    """Pipeline orchestrator. Stateful across calls."""

    def __init__(
        self,
        *,
        stt: STTEngine,
        tts: TTSEngine,
        on_transcript: Callable[[str], None],
        on_audio: Callable[[bytes], None],
        flush_after_silent_ms: int = 600,
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._on_transcript = on_transcript
        self._on_audio = on_audio
        self._flush_threshold = flush_after_silent_ms
        self._last_voice_ms = 0
        self._frames_received = 0
        self._speaking = False
        self._queue: deque[VoiceTurn] = deque()
        self.barge_ins: int = 0

    # ─── inbound (microphone) ──────────────────────────────────────────

    def push_frame(self, frame: VoiceFrame, *, has_voice: bool) -> None:
        """Push one captured frame.

        *has_voice* is the VAD's verdict for the frame. When False for
        long enough we flush the STT buffer; when True while we are
        speaking, we trigger barge-in.
        """
        self._frames_received += 1
        if has_voice and self._speaking:
            # Barge-in: cancel current speech.
            self._speaking = False
            self._queue.clear()
            self.barge_ins += 1
        if has_voice:
            self._stt.feed(frame)
            self._last_voice_ms = frame.timestamp_ms
        else:
            silent = frame.timestamp_ms - self._last_voice_ms
            if silent >= self._flush_threshold:
                self._flush()

    def _flush(self) -> None:
        text = self._stt.finalize()
        self._stt.reset()
        if text.strip():
            self._on_transcript(text.strip())

    # ─── outbound (speak) ──────────────────────────────────────────────

    def speak(self, turn: VoiceTurn) -> None:
        """Enqueue *turn* for TTS playback."""
        self._queue.append(turn)
        self._drain()

    def _drain(self) -> None:
        while self._queue and not self._speaking:
            turn = self._queue.popleft()
            self._speaking = True
            try:
                pcm = self._tts.render(turn)
            finally:
                self._speaking = False
            self._on_audio(pcm)

    # ─── inspection ────────────────────────────────────────────────────

    def queued_turns(self) -> int:
        return len(self._queue)
