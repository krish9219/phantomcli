"""
PhantomCLI Media — image generation and text-to-speech

Image providers : FAL.ai · OpenAI DALL-E · Stability AI · Replicate
TTS providers   : ElevenLabs · OpenAI TTS · PlayHT
"""

import os
import json
import time
import platform
import subprocess
import requests
from datetime import datetime
from omnicli.memory import get_config

_MEDIA_DIR = os.path.expanduser("~/phantom_media")


def _media_dir() -> str:
    os.makedirs(_MEDIA_DIR, exist_ok=True)
    return _MEDIA_DIR


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ─── IMAGE GENERATION ─────────────────────────────────────────────────────────

def generate_image(prompt: str) -> tuple[bool, str]:
    """Generate an image from a text prompt. Returns (success, path_or_error)."""
    provider = get_config("image_provider", "fal")
    dispatch = {
        "fal":       _image_fal,
        "openai":    _image_openai,
        "stability": _image_stability,
        "replicate": _image_replicate,
    }
    fn = dispatch.get(provider)
    if not fn:
        return False, f"Unknown image provider: `{provider}`. Run `python run.py setup` → Image APIs."
    return fn(prompt)


def _image_fal(prompt: str) -> tuple[bool, str]:
    key   = get_config("fal_api_key", "")
    model = get_config("fal_model", "fal-ai/flux/schnell")
    if not key:
        return False, "FAL.ai API key not set. Run `python run.py setup` → Image APIs."
    try:
        r = requests.post(
            f"https://fal.run/{model}",
            headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
            json={"prompt": prompt, "image_size": "square_hd", "num_images": 1},
            timeout=90,
        )
        if not r.ok:
            return False, f"FAL.ai error {r.status_code}: {r.text[:200]}"
        images = r.json().get("images", [])
        if not images:
            return False, "FAL.ai returned no images."
        return _download_image(images[0].get("url", ""), "fal")
    except Exception as e:
        return False, f"FAL.ai error: {e}"


def _image_openai(prompt: str) -> tuple[bool, str]:
    key   = get_config("openai_image_key", "")
    model = get_config("openai_image_model", "dall-e-3")
    if not key:
        return False, "OpenAI Image API key not set. Run `python run.py setup` → Image APIs."
    try:
        from openai import OpenAI
        response = OpenAI(api_key=key).images.generate(
            model=model, prompt=prompt, n=1, size="1024x1024",
        )
        return _download_image(response.data[0].url, "dalle")
    except Exception as e:
        return False, f"OpenAI DALL-E error: {e}"


def _image_stability(prompt: str) -> tuple[bool, str]:
    key = get_config("stability_key", "")
    if not key:
        return False, "Stability AI key not set. Run `python run.py setup` → Image APIs."
    try:
        r = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/core",
            headers={"Authorization": f"Bearer {key}", "Accept": "image/*"},
            data={"prompt": prompt, "output_format": "png"},
            timeout=60,
        )
        if not r.ok:
            return False, f"Stability AI error {r.status_code}: {r.text[:200]}"
        path = os.path.join(_media_dir(), f"phantom_img_{_ts()}_stability.png")
        with open(path, "wb") as f:
            f.write(r.content)
        _open_file(path)
        return True, path
    except Exception as e:
        return False, f"Stability AI error: {e}"


def _image_replicate(prompt: str) -> tuple[bool, str]:
    key = get_config("replicate_key", "")
    if not key:
        return False, "Replicate API key not set. Run `python run.py setup` → Image APIs."
    try:
        r = requests.post(
            "https://api.replicate.com/v1/models/stability-ai/sdxl/predictions",
            headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
            json={"input": {"prompt": prompt, "width": 1024, "height": 1024}},
            timeout=15,
        )
        if not r.ok:
            return False, f"Replicate error {r.status_code}: {r.text[:200]}"
        pred = r.json()
        poll = pred.get("urls", {}).get("get", "")
        if not poll:
            return False, "Replicate: unexpected response format."
        for _ in range(30):
            time.sleep(3)
            pr = requests.get(poll, headers={"Authorization": f"Token {key}"}, timeout=10)
            if not pr.ok:
                continue
            data   = pr.json()
            status = data.get("status")
            if status == "succeeded":
                output = data.get("output", [])
                return _download_image(output[0], "replicate") if output else (False, "Replicate: no output.")
            if status in ("failed", "canceled"):
                return False, f"Replicate: prediction {status}. {data.get('error', '')}"
        return False, "Replicate: timed out waiting for result (90s)."
    except Exception as e:
        return False, f"Replicate error: {e}"


def _download_image(url: str, provider: str) -> tuple[bool, str]:
    if not url:
        return False, "No image URL returned."
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        ext  = "jpg" if "jpeg" in r.headers.get("content-type", "") else "png"
        path = os.path.join(_media_dir(), f"phantom_img_{_ts()}_{provider}.{ext}")
        with open(path, "wb") as f:
            f.write(r.content)
        _open_file(path)
        return True, path
    except Exception as e:
        return False, f"Failed to save image: {e}"


# ─── TEXT-TO-SPEECH ───────────────────────────────────────────────────────────

def generate_tts(text: str) -> tuple[bool, str]:
    """Synthesise speech from text. Returns (success, saved_path_or_error)."""
    provider = get_config("voice_tts_provider", "elevenlabs")
    dispatch = {
        "elevenlabs": _tts_elevenlabs,
        "openai":     _tts_openai,
        "playht":     _tts_playht,
    }
    fn = dispatch.get(provider)
    if not fn:
        return False, f"Unknown TTS provider: `{provider}`. Run `python run.py setup` → Voice APIs."
    return fn(text)


def _tts_elevenlabs(text: str) -> tuple[bool, str]:
    key        = get_config("elevenlabs_key", "")
    voice_name = get_config("elevenlabs_voice", "Rachel")
    if not key:
        return False, "ElevenLabs API key not set. Run `python run.py setup` → Voice APIs."
    try:
        voice_id = "21m00Tcm4TlvDq8ikWAM"  # Rachel default
        vr = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=10)
        if vr.ok:
            for v in vr.json().get("voices", []):
                if v.get("name", "").lower() == voice_name.lower():
                    voice_id = v["voice_id"]
                    break

        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": key, "Accept": "audio/mpeg", "Content-Type": "application/json"},
            json={
                "text": text[:5000],
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=60,
        )
        if not r.ok:
            return False, f"ElevenLabs error {r.status_code}: {r.text[:200]}"
        path = os.path.join(_media_dir(), f"phantom_tts_{_ts()}_elevenlabs.mp3")
        with open(path, "wb") as f:
            f.write(r.content)
        _play_audio(path)
        return True, path
    except Exception as e:
        return False, f"ElevenLabs error: {e}"


def _tts_openai(text: str) -> tuple[bool, str]:
    key   = get_config("openai_tts_key", "")
    model = get_config("openai_tts_model", "tts-1")
    voice = get_config("openai_tts_voice", "nova")
    if not key:
        return False, "OpenAI TTS API key not set. Run `python run.py setup` → Voice APIs."
    try:
        from openai import OpenAI
        response = OpenAI(api_key=key).audio.speech.create(
            model=model, voice=voice, input=text[:4096],
        )
        path = os.path.join(_media_dir(), f"phantom_tts_{_ts()}_openai.mp3")
        response.stream_to_file(path)
        _play_audio(path)
        return True, path
    except Exception as e:
        return False, f"OpenAI TTS error: {e}"


def _tts_playht(text: str) -> tuple[bool, str]:
    key     = get_config("playht_key", "")
    user_id = get_config("playht_user_id", "")
    if not key or not user_id:
        return False, "PlayHT requires both API key and User ID. Run `python run.py setup` → Voice APIs."
    try:
        r = requests.post(
            "https://api.play.ht/api/v2/tts",
            headers={
                "X-USER-ID": user_id,
                "AUTHORIZATION": key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json={
                "text": text[:5000],
                "voice": "s3://voice-cloning-zero-shot/d9ff78ba-d016-47f6-b0ef-dd630f59414e/female-cs/manifest.json",
                "output_format": "mp3",
                "voice_engine": "Play3.0",
            },
            stream=True,
            timeout=90,
        )
        if not r.ok:
            return False, f"PlayHT error {r.status_code}: {r.text[:200]}"

        audio_url = None
        for line in r.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8") if isinstance(line, bytes) else line
            if decoded.startswith("data:"):
                try:
                    data = json.loads(decoded[5:].strip())
                    if data.get("stage") == "complete" and data.get("url"):
                        audio_url = data["url"]
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

        if not audio_url:
            return False, "PlayHT: no audio URL in response stream."

        ar = requests.get(audio_url, timeout=30)
        ar.raise_for_status()
        path = os.path.join(_media_dir(), f"phantom_tts_{_ts()}_playht.mp3")
        with open(path, "wb") as f:
            f.write(ar.content)
        _play_audio(path)
        return True, path
    except Exception as e:
        return False, f"PlayHT error: {e}"


# ─── OS HELPERS ───────────────────────────────────────────────────────────────

def _open_file(path: str):
    """Open a file with the system default viewer (non-blocking)."""
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Linux":
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        pass


def _play_audio(path: str):
    """Play an audio file with the system default player (non-blocking)."""
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Linux":
            for player in ("mpg123", "aplay", "paplay", "xdg-open"):
                try:
                    subprocess.Popen([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break
                except FileNotFoundError:
                    continue
        elif system == "Windows":
            import winsound  # type: ignore[import]
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass
