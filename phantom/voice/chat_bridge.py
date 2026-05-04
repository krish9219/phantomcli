"""Voice → chat bridge.

Wires :class:`phantom.voice.loop.VoiceLoop` into a chat-style callback
loop:

1. Operator speaks; VAD frames flow into the loop.
2. After silence threshold, the loop flushes and emits a transcript.
3. The bridge calls ``on_user_message(transcript)`` — typically the
   chat agent's "send a user turn" method.
4. The bridge takes the agent's reply (string or stream), wraps each
   chunk in a :class:`VoiceTurn`, and feeds it back to the loop for
   TTS playback.

The bridge is engine-agnostic: any callable that takes a string and
returns a string (or yields strings) plugs in. Tests exercise the
full input → STT → bridge → reply → TTS round-trip with stub engines.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from phantom.voice.loop import (
    STTEngine,
    TTSEngine,
    VoiceFrame,
    VoiceLoop,
    VoiceTurn,
)

__all__ = [
    "ChatBridgeError",
    "VoiceChatBridge",
    "build_default_bridge",
]

log = logging.getLogger("phantom.voice.bridge")


class ChatBridgeError(RuntimeError):
    """Raised when the agent reply callback fails."""


ReplyFn = Callable[[str], str]
"""``reply_fn(user_text) -> assistant_text``.

For a streaming agent, return the full concatenated reply (the bridge
does not yet stream TTS chunks; that's a v1.1 feature).
"""


@dataclass
class VoiceChatBridge:
    """Glue between :class:`VoiceLoop` and a chat-style reply function."""

    stt: STTEngine
    tts: TTSEngine
    reply_fn: ReplyFn
    on_audio: Callable[[bytes], None]
    flush_after_silent_ms: int = 600
    voice_name: str = "default"
    speed: float = 1.0

    _loop: VoiceLoop = field(init=False)
    _transcripts: deque[str] = field(default_factory=deque, init=False)
    _replies: deque[str] = field(default_factory=deque, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _closed: bool = field(default=False, init=False)
    errors: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._loop = VoiceLoop(
            stt=self.stt,
            tts=self.tts,
            on_transcript=self._on_transcript,
            on_audio=self.on_audio,
            flush_after_silent_ms=self.flush_after_silent_ms,
        )

    # ── public ──────────────────────────────────────────────────────

    def push_frame(self, frame: VoiceFrame, *, has_voice: bool) -> None:
        if self._closed:
            raise ChatBridgeError("bridge closed")
        self._loop.push_frame(frame, has_voice=has_voice)

    def speak(self, text: str) -> None:
        """Manual TTS injection (e.g. greetings, error notifications)."""
        if not text:
            return
        self._loop.speak(VoiceTurn(text=text, voice=self.voice_name, speed=self.speed))

    def close(self) -> None:
        self._closed = True

    @property
    def transcripts(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._transcripts)

    @property
    def replies(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._replies)

    def barge_ins(self) -> int:
        return self._loop.barge_ins

    # ── internal ────────────────────────────────────────────────────

    def _on_transcript(self, text: str) -> None:
        with self._lock:
            self._transcripts.append(text)
        try:
            reply = self.reply_fn(text)
        except Exception as e:
            self.errors.append(f"{type(e).__name__}: {e}")
            log.exception("bridge reply_fn raised")
            return
        if not isinstance(reply, str) or not reply.strip():
            return
        with self._lock:
            self._replies.append(reply)
        self._loop.speak(VoiceTurn(text=reply, voice=self.voice_name, speed=self.speed))


# ─── default bridge for the chat REPL ───────────────────────────────────────


def build_default_bridge(
    *,
    reply_fn: ReplyFn,
    on_audio: Optional[Callable[[bytes], None]] = None,
    stt: Optional[STTEngine] = None,
    tts: Optional[TTSEngine] = None,
) -> VoiceChatBridge:
    """Construct a bridge with sensible defaults.

    If no engines are supplied, we use the stub engines so the bridge
    works in CI / no-audio environments. Production callers pass real
    Whisper + Piper engines from :mod:`phantom.voice.engines`.
    """
    from phantom.voice.engines.stub import StubSTT, StubTTS

    return VoiceChatBridge(
        stt=stt or StubSTT(),
        tts=tts or StubTTS(),
        reply_fn=reply_fn,
        on_audio=on_audio or (lambda _pcm: None),
    )
