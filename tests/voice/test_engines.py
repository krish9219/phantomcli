"""Tests for the voice engine adapters using injected stub models.

We avoid importing faster-whisper and piper at runtime; the tests pass
``model=...`` so the adapters never touch the real ML libraries. This
verifies the adapter code paths (PCM conversion, buffering, error
handling) without the multi-hundred-MB model download.
"""

from __future__ import annotations

import struct

import pytest

from phantom.voice import VoiceFrame, VoiceTurn
from phantom.voice.engines.piper import PiperTTS
from phantom.voice.engines.whisper import FasterWhisperSTT


# ─── FasterWhisperSTT ────────────────────────────────────────────────────────


class _FakeWhisperModel:
    def __init__(self):
        self.transcribe_calls = 0

    def transcribe(self, audio, *, language=None, beam_size=1, vad_filter=True):
        self.transcribe_calls += 1

        class _Seg:
            text = " hello world "
        return [_Seg()], {}


class TestFasterWhisperSTT:
    def test_feed_then_finalize(self):
        model = _FakeWhisperModel()
        stt = FasterWhisperSTT(model=model)
        # 1024 PCM-16 samples = 2048 bytes of zero audio.
        zeros = struct.pack("<" + "h" * 1024, *([0] * 1024))
        stt.feed(VoiceFrame(pcm=zeros, sample_rate=16000, timestamp_ms=0))
        out = stt.finalize()
        assert out == "hello world"
        assert model.transcribe_calls == 1

    def test_empty_buffer_returns_empty(self):
        stt = FasterWhisperSTT(model=_FakeWhisperModel())
        assert stt.finalize() == ""

    def test_wrong_sample_rate_rejected(self):
        stt = FasterWhisperSTT(model=_FakeWhisperModel())
        with pytest.raises(ValueError, match="16000"):
            stt.feed(VoiceFrame(pcm=b"\x00\x00", sample_rate=8000))

    def test_reset_clears_buffer(self):
        stt = FasterWhisperSTT(model=_FakeWhisperModel())
        stt.feed(VoiceFrame(pcm=struct.pack("<h", 100), sample_rate=16000))
        stt.reset()
        assert stt.finalize() == ""


# ─── PiperTTS ────────────────────────────────────────────────────────────────


class _FakePiperVoice:
    def __init__(self):
        self.synth_calls: list = []

    def synthesize_stream_raw(self, text, *, speaker_id=None, length_scale=1.0):
        self.synth_calls.append((text, speaker_id, length_scale))
        # Yield two chunks of fake PCM.
        yield b"AAAA"
        yield b"BBBB"


class TestPiperTTS:
    def test_render_concatenates_chunks(self):
        model = _FakePiperVoice()
        tts = PiperTTS(model=model)
        out = tts.render(VoiceTurn(text="hello"))
        assert out == b"AAAABBBB"
        assert model.synth_calls[0][0] == "hello"

    def test_empty_turn_returns_empty(self):
        tts = PiperTTS(model=_FakePiperVoice())
        assert tts.render(VoiceTurn(text="   ")) == b""

    def test_speed_translates_to_length_scale(self):
        model = _FakePiperVoice()
        tts = PiperTTS(model=model)
        tts.render(VoiceTurn(text="x", speed=2.0))
        # length_scale = 1 / speed ⇒ 0.5 for speed 2.0
        _text, _spk, length_scale = model.synth_calls[0]
        assert length_scale == pytest.approx(0.5)

    def test_speaker_index_passed_through(self):
        model = _FakePiperVoice()
        tts = PiperTTS(model=model, speaker=3)
        tts.render(VoiceTurn(text="x"))
        assert model.synth_calls[0][1] == 3

    def test_construction_requires_model_or_path(self):
        with pytest.raises(ValueError):
            PiperTTS()
