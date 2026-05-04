"""Tests for the voice/dictate MVP."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from phantom.voice.dictate import (
    DictateBackendError,
    DictateResult,
    dictate,
    discover_recorder,
    transcribe,
)


def test_unknown_backend_rejected(tmp_path: Path):
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")
    with pytest.raises(DictateBackendError):
        transcribe(audio, backend="does-not-exist")


def test_stub_backend_returns_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PHANTOM_DICTATE_STUB_TEXT", raising=False)
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")
    assert transcribe(audio, backend="stub") == "stub transcript"


def test_stub_backend_honours_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHANTOM_DICTATE_STUB_TEXT", "hello phantom")
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")
    assert transcribe(audio, backend="stub") == "hello phantom"


def test_dictate_with_supplied_audio_skips_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHANTOM_DICTATE_BACKEND", "stub")
    monkeypatch.setenv("PHANTOM_DICTATE_STUB_TEXT", "skip recording")
    audio = tmp_path / "in.wav"
    audio.write_bytes(b"")
    r = dictate(seconds=1.0, audio_path=audio)
    assert isinstance(r, DictateResult)
    assert r.text == "skip recording"
    assert r.backend == "stub"
    assert r.audio_path == str(audio)


def test_openai_backend_requires_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PHANTOM_OPENAI_API_KEY", raising=False)
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"")
    with pytest.raises(DictateBackendError, match="OPENAI_API_KEY"):
        transcribe(audio, backend="openai-whisper")


def test_discover_recorder_returns_tuple_or_none():
    rec = discover_recorder()
    assert rec is None or (isinstance(rec, tuple) and len(rec) == 2)
