"""Stage 6 smoke test."""

from __future__ import annotations

import json

import pytest

from phantom.canvas import CanvasNode, render_to_dict
from phantom.pwa import build_manifest, build_service_worker
from phantom.voice import VoiceFrame, VoiceLoop, VoiceTurn


class _StubSTT:
    def __init__(self): self.t = ["hello"]
    def feed(self, frame): pass
    def finalize(self): return self.t.pop(0) if self.t else ""
    def reset(self): pass


class _StubTTS:
    def render(self, turn): return b"PCM"


@pytest.mark.stage6
def test_voice_loop_round_trip():
    transcripts: list[str] = []
    audio: list[bytes] = []
    loop = VoiceLoop(
        stt=_StubSTT(), tts=_StubTTS(),
        on_transcript=transcripts.append, on_audio=audio.append,
        flush_after_silent_ms=100,
    )
    loop.push_frame(VoiceFrame(pcm=b"X", timestamp_ms=0), has_voice=True)
    loop.push_frame(VoiceFrame(pcm=b"", timestamp_ms=200), has_voice=False)
    assert transcripts == ["hello"]
    loop.speak(VoiceTurn(text="ok"))
    assert audio == [b"PCM"]


@pytest.mark.stage6
def test_canvas_node_serialises():
    root = CanvasNode(kind="text", props={"value": "hi"})
    d = render_to_dict(root)
    json.dumps(d)
    assert d["kind"] == "text"


@pytest.mark.stage6
def test_pwa_manifest_is_json_serialisable():
    m = build_manifest()
    json.dumps(m)
    assert m["display"] == "standalone"


@pytest.mark.stage6
def test_pwa_service_worker_is_string():
    sw = build_service_worker()
    assert isinstance(sw, str) and "phantom-app-shell" in sw


@pytest.mark.stage6
def test_phantom_stage_advanced_to_6_or_higher():
    import phantom
    assert phantom.feature_flags()["stage"] >= 6
