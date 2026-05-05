"""``phantom dictate`` — record audio, transcribe via Whisper API.

Minimal MVP. Not the realtime VAD loop (that's Stage 6 phantom.voice.loop);
this is the "press a key, talk, hit a key, get text" path that puts a
voice surface in users' hands today.

Backend resolution
------------------

The default backend is OpenAI's Whisper API (``whisper-1``). Override
with the ``PHANTOM_DICTATE_BACKEND`` env var or the ``--backend`` flag:

* ``openai-whisper`` — POST audio to /v1/audio/transcriptions.
* ``stub``           — returns a fixed string. Used by tests so they
                       don't need network or an API key.

Audio capture
-------------

Uses ``sox`` if available, then ``arecord``, then ``parecord``. We
don't ship a Python audio library — the deps are heavy and the system
recorders are already installed everywhere we deploy.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

__all__ = [
    "DictateBackendError",
    "DictateResult",
    "discover_recorder",
    "record_to_wav",
    "transcribe",
    "dictate",
]

log = logging.getLogger("phantom.voice.dictate")


class DictateBackendError(RuntimeError):
    """Raised when the chosen backend cannot fulfil a request."""


@dataclass(frozen=True, slots=True)
class DictateResult:
    text: str
    backend: str
    duration_s: float
    audio_path: str  # tmpfile path (caller owns cleanup)


# ─── recorder selection ───────────────────────────────────────────────────────


_RECORDER_CMDS: list[tuple[str, list[str]]] = [
    ("sox",      ["sox", "-d", "-r", "16000", "-c", "1", "{out}", "trim", "0", "{secs}"]),
    ("arecord",  ["arecord", "-q", "-r", "16000", "-c", "1", "-f", "S16_LE", "-d", "{secs}", "{out}"]),
    ("parecord", ["parecord", "--rate=16000", "--channels=1", "--format=s16le", "{out}"]),
]


def discover_recorder() -> Optional[tuple[str, list[str]]]:
    """Return the first available command-line recorder + its argv template.

    Order: sox (cross-platform), arecord (Linux ALSA), parecord (Linux PulseAudio).
    Returns ``None`` if no command-line recorder is on PATH; the caller
    falls back to the Python ``sounddevice`` backend (Windows + cross-platform).
    """
    for name, argv in _RECORDER_CMDS:
        if shutil.which(name):
            return name, argv
    return None


def _record_via_sounddevice(seconds: float, out_path: Path) -> Path:
    """Pure-Python recorder. Works on Windows / macOS / Linux when the
    user has installed the optional ``sounddevice`` package + libportaudio.

    The Linux command-line recorders are preferred (no extra deps). This
    backend is the cross-platform fallback — particularly important on
    Windows, where neither ``sox`` nor ``arecord`` ship by default.
    """
    try:
        import sounddevice as sd
        import wave
    except ImportError as e:
        raise DictateBackendError(
            "no audio recorder found. install one of:\n"
            "  • sox (Linux/macOS):    apt install sox  /  brew install sox\n"
            "  • arecord (Linux):      apt install alsa-utils\n"
            "  • parecord (Linux):     apt install pulseaudio-utils\n"
            "  • sounddevice (any OS): pip install sounddevice"
        ) from e

    sample_rate = 16000
    n_frames = int(max(1, seconds) * sample_rate)
    log.debug("recording with sounddevice: %d frames @ %d Hz", n_frames, sample_rate)
    try:
        recording = sd.rec(n_frames, samplerate=sample_rate, channels=1, dtype="int16")
        sd.wait()
    except Exception as e:
        raise DictateBackendError(f"sounddevice recording failed: {e}") from e

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)        # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(recording.tobytes())
    return out_path


def record_to_wav(seconds: float, *, out_path: Optional[Path] = None) -> Path:
    """Record `seconds` of mono 16 kHz WAV. Returns the file path.

    Tries command-line recorders first (cheaper, no Python deps), falls
    back to the ``sounddevice`` Python backend on Windows or wherever
    the CLI tools aren't available.
    """
    out = out_path or Path(tempfile.mkstemp(suffix=".wav", prefix="phantom-dictate-")[1])
    rec = discover_recorder()

    if rec is not None:
        name, template = rec
        argv = [arg.format(out=str(out), secs=str(int(max(1, seconds)))) for arg in template]
        log.debug("recording with %s: %s", name, argv)
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            raise DictateBackendError(f"{name} failed: {proc.stderr.strip()}")
        return out

    # No CLI recorder on PATH — fall back to sounddevice (Python lib).
    return _record_via_sounddevice(seconds, out)


# ─── transcription backends ───────────────────────────────────────────────────


def _backend_openai(audio_path: Path) -> str:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("PHANTOM_OPENAI_API_KEY")
    if not api_key:
        raise DictateBackendError("OPENAI_API_KEY not set")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/audio/transcriptions"
    boundary = "----PhantomDictate"
    body_parts: list[bytes] = []
    body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n".encode())
    body_parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{audio_path.name}\"\r\n"
        f"Content-Type: audio/wav\r\n\r\n".encode()
    )
    body_parts.append(audio_path.read_bytes())
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(body_parts)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DictateBackendError(f"whisper API HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        raise DictateBackendError(f"whisper API unreachable: {e.reason}")
    text = payload.get("text", "")
    if not isinstance(text, str):
        raise DictateBackendError(f"whisper API returned unexpected payload: {payload!r}")
    return text.strip()


def _backend_stub(_audio_path: Path) -> str:
    return os.environ.get("PHANTOM_DICTATE_STUB_TEXT", "stub transcript")


_BACKENDS = {
    "openai-whisper": _backend_openai,
    "stub": _backend_stub,
}


def transcribe(audio_path: Path, *, backend: str = "openai-whisper") -> str:
    fn = _BACKENDS.get(backend)
    if fn is None:
        raise DictateBackendError(f"unknown backend: {backend!r} (have {sorted(_BACKENDS)})")
    return fn(audio_path)


# ─── orchestration ───────────────────────────────────────────────────────────


def dictate(
    seconds: float = 5.0,
    *,
    backend: Optional[str] = None,
    audio_path: Optional[Path] = None,
) -> DictateResult:
    """Record `seconds`, transcribe, return :class:`DictateResult`."""
    chosen_backend = backend or os.environ.get("PHANTOM_DICTATE_BACKEND") or "openai-whisper"
    if audio_path is None:
        audio_path = record_to_wav(seconds)
    text = transcribe(audio_path, backend=chosen_backend)
    return DictateResult(
        text=text,
        backend=chosen_backend,
        duration_s=float(seconds),
        audio_path=str(audio_path),
    )
