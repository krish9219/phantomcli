"""
PhantomCLI Voice Module — TTS + STT
TTS: pyttsx3/espeak (free) → ElevenLabs (if key set)
STT: Whisper local → Google Speech API fallback
Code blocks are stripped before speaking — only prose is read aloud.
"""
import os
import re
import sys
import threading
import tempfile

from omnicli.memory import get_config, save_config


# ── Text cleaning ──────────────────────────────────────────────────────────────

def strip_for_speech(text: str) -> str:
    """Remove code, markdown, URLs, HUD chars — keep only readable prose."""
    # Fenced code blocks (``` ... ```)
    text = re.sub(r"```[\s\S]*?```", " ", text)
    # Inline code (`code`)
    text = re.sub(r"`[^`\n]+`", " ", text)
    # Markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold / italic
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}([^_\n]+)_{1,2}", r"\1", text)
    # Markdown tables — pipes & separator rows
    text = re.sub(r"\|[-: ]+\|[-: |]*", " ", text)
    text = re.sub(r"\|", " ", text)
    # URLs
    text = re.sub(r"https?://\S+", "a link", text)
    # HUD / box-drawing chars
    text = re.sub(r"[║╔╗╚╝╠╣═╬╭╮╰╯▰▶◈►◆◇■□▓▒░█]", "", text)
    # Emoji (basic block)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    # Multiple spaces / blank lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── TTS ───────────────────────────────────────────────────────────────────────

def speak(text: str):
    """Speak text non-blocking. Strips code before reading."""
    if not is_voice_enabled():
        return
    clean = strip_for_speech(text)
    if not clean or len(clean) < 5:
        return
    threading.Thread(target=_speak_sync, args=(clean,), daemon=True).start()


def _speak_sync(text: str):
    elevenlabs_key = get_config("elevenlabs_key", "")
    if elevenlabs_key:
        try:
            _speak_elevenlabs(text, elevenlabs_key)
            return
        except Exception:
            pass
    try:
        _speak_pyttsx3(text)
        return
    except Exception:
        pass
    try:
        _speak_espeak(text)
    except Exception:
        pass


def _speak_elevenlabs(text: str, api_key: str):
    import requests
    import subprocess
    voice_id = get_config("elevenlabs_voice_id", "") or "21m00Tcm4TlvDq8ikWAM"
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text[:4000],
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=30,
    )
    r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(r.content)
        tmp = f.name
    try:
        for player in ("mpg123", "mpg321", "ffplay", "cvlc", "aplay"):
            try:
                subprocess.run([player, "-q", tmp], capture_output=True, timeout=120)
                return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
    finally:
        try: os.unlink(tmp)
        except: pass


def _speak_pyttsx3(text: str):
    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty("rate", 175)
    engine.setProperty("volume", 1.0)
    engine.say(text[:4000])
    engine.runAndWait()
    engine.stop()


def _speak_espeak(text: str):
    import subprocess
    subprocess.run(["espeak", "-s", "150", "-v", "en", text[:4000]],
                   capture_output=True, timeout=120)


# ── STT ───────────────────────────────────────────────────────────────────────

def listen() -> str:
    """Record mic, return transcribed text. Tries Whisper then Google."""
    sys.stdout.write("\033[36m  🎤 Listening...\033[0m\n")
    sys.stdout.flush()
    try:
        return _listen_whisper()
    except Exception:
        pass
    try:
        return _listen_google()
    except Exception:
        pass
    sys.stdout.write("\033[33m  ⚠  Voice capture failed — type instead\033[0m\n")
    sys.stdout.flush()
    return ""


def _listen_whisper() -> str:
    import whisper
    import sounddevice as sd
    import numpy as np
    import scipy.io.wavfile as wav

    sample_rate = 16000
    duration    = 10  # seconds

    audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate,
                   channels=1, dtype="float32")
    sd.wait()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav.write(f.name, sample_rate, (audio * 32767).astype(np.int16))
        tmp = f.name

    try:
        model  = whisper.load_model("base")
        result = model.transcribe(tmp, language="en")
        return result["text"].strip()
    finally:
        try: os.unlink(tmp)
        except: pass


def _listen_google() -> str:
    import speech_recognition as sr
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.5)
        audio = r.listen(source, timeout=10, phrase_time_limit=10)
    return r.recognize_google(audio)


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_voice_enabled() -> bool:
    return get_config("voice_mode", "off") == "on"


def toggle_voice(enabled: bool):
    save_config("voice_mode", "on" if enabled else "off")
    state = "ON" if enabled else "OFF"
    sys.stdout.write(f"\n  \033[36m◈  Voice mode {state}\033[0m\n\n")
    sys.stdout.flush()
