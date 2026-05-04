"""Tests for :mod:`phantom.voice.loop`."""

from __future__ import annotations

from phantom.voice import STTEngine, TTSEngine, VoiceFrame, VoiceLoop, VoiceTurn


class _StubSTT:
    def __init__(self):
        self.fed: list[VoiceFrame] = []
        self.transcripts = ["hello world"]

    def feed(self, frame): self.fed.append(frame)
    def finalize(self):
        return self.transcripts.pop(0) if self.transcripts else ""
    def reset(self):
        self.fed = []


class _StubTTS:
    def __init__(self): self.calls: list[VoiceTurn] = []
    def render(self, turn):
        self.calls.append(turn)
        return b"PCM_" + turn.text.encode()


class TestVoiceLoopFlush:
    def test_flush_after_silence(self):
        transcripts: list[str] = []
        audio: list[bytes] = []
        loop = VoiceLoop(
            stt=_StubSTT(), tts=_StubTTS(),
            on_transcript=transcripts.append, on_audio=audio.append,
            flush_after_silent_ms=200,
        )
        # Speak briefly then go silent past threshold.
        loop.push_frame(VoiceFrame(pcm=b"X", timestamp_ms=0), has_voice=True)
        loop.push_frame(VoiceFrame(pcm=b"X", timestamp_ms=100), has_voice=True)
        loop.push_frame(VoiceFrame(pcm=b"", timestamp_ms=400), has_voice=False)
        assert transcripts == ["hello world"]

    def test_no_flush_under_threshold(self):
        transcripts: list[str] = []
        loop = VoiceLoop(
            stt=_StubSTT(), tts=_StubTTS(),
            on_transcript=transcripts.append, on_audio=lambda b: None,
            flush_after_silent_ms=600,
        )
        loop.push_frame(VoiceFrame(pcm=b"X", timestamp_ms=0), has_voice=True)
        loop.push_frame(VoiceFrame(pcm=b"", timestamp_ms=300), has_voice=False)
        assert transcripts == []


class TestVoiceLoopSpeak:
    def test_speak_renders_through_tts(self):
        audio: list[bytes] = []
        loop = VoiceLoop(
            stt=_StubSTT(), tts=_StubTTS(),
            on_transcript=lambda t: None, on_audio=audio.append,
        )
        loop.speak(VoiceTurn(text="hi"))
        assert audio == [b"PCM_hi"]

    def test_queues_overflow_drained_in_order(self):
        audio: list[bytes] = []
        loop = VoiceLoop(
            stt=_StubSTT(), tts=_StubTTS(),
            on_transcript=lambda t: None, on_audio=audio.append,
        )
        loop.speak(VoiceTurn(text="one"))
        loop.speak(VoiceTurn(text="two"))
        loop.speak(VoiceTurn(text="three"))
        assert audio == [b"PCM_one", b"PCM_two", b"PCM_three"]


class TestBargeIn:
    def test_voice_during_speech_cancels_queue(self):
        # Build a TTS that doesn't actually finish synchronously — set
        # the loop's _speaking flag manually to model "TTS in progress".
        loop = VoiceLoop(
            stt=_StubSTT(), tts=_StubTTS(),
            on_transcript=lambda t: None, on_audio=lambda b: None,
        )
        loop._speaking = True  # noqa: SLF001 — simulate ongoing TTS
        loop._queue.append(VoiceTurn(text="should-be-cancelled"))  # noqa: SLF001
        loop.push_frame(VoiceFrame(pcm=b"X", timestamp_ms=10), has_voice=True)
        assert loop.barge_ins == 1
        assert loop.queued_turns() == 0
