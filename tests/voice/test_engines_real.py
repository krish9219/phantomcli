"""Real engine integration tests for voice.

These import the actual ``faster_whisper`` and ``piper`` libraries and
run a synthesised audio path through the adapters end-to-end. They
are gated by ``pytest -m voice_real`` so they don't run on every CI
build (the whisper model download is ~150 MB).

The whisper test uses the smallest model (``tiny.en``, ~75 MB) and
synthesises an audio buffer in-process — no microphone, no network
beyond the one-time HuggingFace download.
"""

from __future__ import annotations

import os
import struct

import pytest

faster_whisper = pytest.importorskip("faster_whisper")
import numpy as np  # noqa: E402

from phantom.voice import VoiceFrame, VoiceTurn  # noqa: E402
from phantom.voice.engines.whisper import FasterWhisperSTT  # noqa: E402


# Mark every test in this file as 'voice_real'. Run explicitly with
# `pytest -m voice_real`. Skipped by default when the
# PHANTOM_VOICE_REAL env var is unset, so CI doesn't fetch the model.
real_voice_enabled = pytest.mark.skipif(
    not os.environ.get("PHANTOM_VOICE_REAL"),
    reason="set PHANTOM_VOICE_REAL=1 to download the model and run",
)


@real_voice_enabled
class TestRealWhisper:
    def test_silent_audio_returns_empty_or_noise(self):
        """A second of silence transcribes to nothing meaningful.

        Specifically: with VAD-filter on, faster-whisper returns 0
        segments. Without VAD it might return a hallucination; we
        accept either (the goal is "the adapter loads the model and
        invokes it"; ASR quality is faster-whisper's job, not ours).
        """
        stt = FasterWhisperSTT(model_size="tiny.en", language="en")
        # 1 s of silence at 16 kHz, mono, int16.
        silence = struct.pack("<" + "h" * 16000, *([0] * 16000))
        stt.feed(VoiceFrame(pcm=silence, sample_rate=16000, timestamp_ms=0))
        out = stt.finalize()
        # We accept any string output (including empty). The point is
        # that the model loaded, the float conversion worked, and the
        # transcribe call returned without raising.
        assert isinstance(out, str)


# ─── Piper ─────────────────────────────────────────────────────────────

# The Piper test path is more involved because piper requires an
# .onnx voice model file (~25 MB). We don't bundle one. Operators who
# want to verify Piper end-to-end:
#
#   wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
#     -O /tmp/voice.onnx
#   PHANTOM_PIPER_MODEL=/tmp/voice.onnx pytest -m voice_real
#
# The test below skips when the env var isn't set.


piper_enabled = pytest.mark.skipif(
    not os.environ.get("PHANTOM_PIPER_MODEL"),
    reason="set PHANTOM_PIPER_MODEL=/path/to/voice.onnx to run",
)


@piper_enabled
class TestRealPiper:
    def test_synthesise_short_phrase(self):
        from phantom.voice.engines.piper import PiperTTS
        tts = PiperTTS(model_path=os.environ["PHANTOM_PIPER_MODEL"])
        out = tts.render(VoiceTurn(text="Hello from Phantom."))
        # PCM-16 mono ≥ a few KB for a sub-second phrase.
        assert len(out) > 4096
        # Must be even (16-bit samples).
        assert len(out) % 2 == 0
