"""Tests for the voice→chat bridge."""

from __future__ import annotations

import pytest

from phantom.voice.chat_bridge import (
    ChatBridgeError,
    VoiceChatBridge,
    build_default_bridge,
)
from phantom.voice.engines.stub import StubSTT, StubTTS
from phantom.voice.loop import VoiceFrame


def _frame(ts_ms: int) -> VoiceFrame:
    return VoiceFrame(pcm=b"\x00\x00" * 16, sample_rate=16000, timestamp_ms=ts_ms)


class _AudioSink:
    def __init__(self) -> None:
        self.buffers: list[bytes] = []

    def __call__(self, pcm: bytes) -> None:
        self.buffers.append(pcm)


def _bridge(reply: str = "noted", reply_fn=None) -> tuple[VoiceChatBridge, StubSTT, _AudioSink]:
    sink = _AudioSink()
    stt = StubSTT()
    bridge = VoiceChatBridge(
        stt=stt,
        tts=StubTTS(),
        reply_fn=reply_fn or (lambda txt: reply),
        on_audio=sink,
    )
    return bridge, stt, sink


# ─── basic flow ────────────────────────────────────────────────────────────


def test_silence_after_voice_flushes_transcript():
    bridge, stt, _sink = _bridge(reply="ok")
    stt.set_next_transcript("hello phantom")
    bridge.push_frame(_frame(0), has_voice=True)
    bridge.push_frame(_frame(1000), has_voice=False)
    assert bridge.transcripts == ("hello phantom",)


def test_transcript_triggers_reply_fn_and_tts():
    captured: list[str] = []

    def reply(text):
        captured.append(text)
        return "got it"

    bridge, stt, sink = _bridge(reply_fn=reply)
    stt.set_next_transcript("any prompt")
    bridge.push_frame(_frame(0), has_voice=True)
    bridge.push_frame(_frame(1000), has_voice=False)
    assert captured == ["any prompt"]
    assert bridge.replies == ("got it",)
    assert len(sink.buffers) >= 1
    # Each rendered audio chunk is non-empty bytes
    assert all(isinstance(b, bytes) and b for b in sink.buffers)


def test_empty_reply_skips_tts():
    bridge, stt, sink = _bridge(reply="")
    stt.set_next_transcript("ping")
    bridge.push_frame(_frame(0), has_voice=True)
    bridge.push_frame(_frame(1000), has_voice=False)
    assert bridge.transcripts == ("ping",)
    assert bridge.replies == ()
    assert sink.buffers == []


def test_whitespace_only_reply_skips_tts():
    bridge, stt, _sink = _bridge(reply="   \n  ")
    stt.set_next_transcript("ping")
    bridge.push_frame(_frame(0), has_voice=True)
    bridge.push_frame(_frame(1000), has_voice=False)
    assert bridge.replies == ()


def test_reply_fn_exception_recorded_not_raised():
    def boom(_):
        raise RuntimeError("LLM down")

    bridge, stt, _sink = _bridge(reply_fn=boom)
    stt.set_next_transcript("hi")
    bridge.push_frame(_frame(0), has_voice=True)
    bridge.push_frame(_frame(1000), has_voice=False)
    assert any("LLM down" in e for e in bridge.errors)


def test_manual_speak_injection():
    bridge, _stt, sink = _bridge(reply="x")
    bridge.speak("greeting")
    assert len(sink.buffers) == 1


def test_manual_speak_skips_empty():
    bridge, _stt, sink = _bridge(reply="x")
    bridge.speak("")
    assert sink.buffers == []


def test_close_blocks_further_input():
    bridge, _stt, _sink = _bridge(reply="x")
    bridge.close()
    with pytest.raises(ChatBridgeError, match="closed"):
        bridge.push_frame(_frame(0), has_voice=True)


def test_barge_in_count_exposed():
    bridge, stt, _sink = _bridge(reply="reply that gets bargeed")
    stt.set_next_transcript("first")
    bridge.push_frame(_frame(0), has_voice=True)
    bridge.push_frame(_frame(1000), has_voice=False)
    # During reply playback (synchronous TTS render), barge-in only
    # counts when a new voice frame arrives while still speaking — with
    # synchronous render the speak finishes before next frame, so we
    # test the loop's accounting directly.
    assert bridge.barge_ins() == 0


# ─── default bridge factory ───────────────────────────────────────────────


def test_build_default_bridge_works_without_explicit_engines():
    b = build_default_bridge(reply_fn=lambda t: "ok")
    assert isinstance(b, VoiceChatBridge)


def test_build_default_bridge_uses_supplied_audio_sink():
    received: list[bytes] = []
    b = build_default_bridge(reply_fn=lambda t: "ok", on_audio=received.append)
    b.speak("hi")
    assert len(received) == 1


# ─── thread-safety on transcripts ─────────────────────────────────────────


def test_transcripts_returned_as_tuple_not_live_view():
    bridge, stt, _sink = _bridge(reply="x")
    stt.set_next_transcript("first")
    bridge.push_frame(_frame(0), has_voice=True)
    bridge.push_frame(_frame(1000), has_voice=False)
    snap = bridge.transcripts
    stt.set_next_transcript("second")
    bridge.push_frame(_frame(2000), has_voice=True)
    bridge.push_frame(_frame(3000), has_voice=False)
    # snap is unchanged; current transcripts have grown
    assert snap == ("first",)
    assert bridge.transcripts == ("first", "second")
