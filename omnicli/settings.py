"""
PhantomCLI Settings — centralised configuration registry
All user-configurable settings with typed defaults and descriptions.
"""

from omnicli.memory import get_config, save_config

# ─── SETTING DEFINITIONS ──────────────────────────────────────────────────────
# Each entry: (key, default, label, description, category, secret)

SETTINGS = [
    # ── Core AI ──────────────────────────────────────────────────────────────
    ("main_url",          "https://api.anthropic.com/v1", "Main Base URL",     "OpenAI-compatible endpoint for the main model",           "core",    False),
    ("main_model",        "claude-opus-4-5",              "Main Model",        "Primary reasoning model (heavy, high quality)",           "core",    False),
    ("router_url",        "https://api.groq.com/openai/v1","Router Base URL",  "Endpoint for the fast routing/classifier model",          "core",    False),
    ("router_model",      "llama3-8b-8192",               "Router Model",      "Fast model that picks the right expert persona",          "core",    False),
    ("default_trust",     "3",                            "Default Trust",     "1=Paranoid 2=Standard 3=Developer 4=God Mode",            "core",    False),
    ("dashboard_port",    "8080",                         "Dashboard Port",    "Port the web dashboard runs on",                          "core",    False),

    # ── Telegram ─────────────────────────────────────────────────────────────
    ("telegram_token",    "",  "Bot Token",               "From @BotFather on Telegram",                              "telegram", True),
    ("telegram_chat_id",  "",  "Chat ID",                 "Your personal chat ID (visit /getUpdates to find it)",     "telegram", False),
    ("telegram_trust",    "2", "Telegram Trust Level",    "Independent trust level for Telegram commands (1–4)",      "telegram", False),

    # ── Image Generation ─────────────────────────────────────────────────────
    ("image_provider",    "fal",   "Image Provider",      "fal | openai | stability | replicate",                     "image",   False),
    ("fal_api_key",       "",      "FAL.ai API Key",       "get.fal.ai — best for FLUX, fast generation",              "image",   True),
    ("fal_model",         "fal-ai/flux/schnell", "FAL Model", "e.g. fal-ai/flux/schnell, fal-ai/flux-pro",           "image",   False),
    ("openai_image_key",  "",      "OpenAI Image API Key", "For DALL-E 3 image generation",                           "image",   True),
    ("openai_image_model","dall-e-3","DALL-E Model",       "dall-e-3 | dall-e-2",                                     "image",   False),
    ("stability_key",     "",      "Stability AI Key",     "platform.stability.ai — Stable Diffusion XL",             "image",   True),
    ("stability_model",   "stable-diffusion-xl-1024-v1-0","Stability Model","SD model ID",                           "image",   False),
    ("replicate_key",     "",      "Replicate API Key",    "replicate.com — run any open model",                      "image",   True),

    # ── Video Generation ─────────────────────────────────────────────────────
    ("video_provider",    "runway", "Video Provider",      "runway | kling | pika | luma",                            "video",   False),
    ("runway_key",        "",       "RunwayML API Key",     "app.runwayml.com — Gen-3 Alpha",                         "video",   True),
    ("runway_model",      "gen3a_turbo", "Runway Model",   "gen3a_turbo | gen3a",                                     "video",   False),
    ("kling_key",         "",       "Kling AI API Key",     "klingai.com — high quality video",                       "video",   True),
    ("pika_key",          "",       "Pika API Key",         "pika.art",                                               "video",   True),
    ("luma_key",          "",       "Luma AI API Key",      "lumalabs.ai — Dream Machine",                            "video",   True),

    # ── Voice / TTS / STT ────────────────────────────────────────────────────
    ("voice_tts_provider","elevenlabs", "TTS Provider",    "elevenlabs | openai | playht | deepgram",                 "voice",   False),
    ("elevenlabs_key",    "",       "ElevenLabs API Key",   "elevenlabs.io — most natural voices",                    "voice",   True),
    ("elevenlabs_voice",  "Rachel", "ElevenLabs Voice",    "Voice name or ID",                                        "voice",   False),
    ("openai_tts_key",    "",       "OpenAI TTS API Key",   "For tts-1 / tts-1-hd models",                           "voice",   True),
    ("openai_tts_model",  "tts-1",  "OpenAI TTS Model",    "tts-1 | tts-1-hd",                                       "voice",   False),
    ("openai_tts_voice",  "nova",   "OpenAI TTS Voice",    "alloy | echo | fable | onyx | nova | shimmer",           "voice",   False),
    ("playht_key",        "",       "PlayHT API Key",       "play.ht — ultra-realistic voices",                       "voice",   True),
    ("playht_user_id",    "",       "PlayHT User ID",       "Required alongside PlayHT API key",                      "voice",   False),

    # ── Voice / STT ──────────────────────────────────────────────────────────
    ("voice_stt_provider","whisper", "STT Provider",       "whisper | deepgram | assemblyai",                         "voice",   False),
    ("deepgram_key",      "",       "Deepgram API Key",     "deepgram.com — real-time speech recognition",            "voice",   True),
    ("assemblyai_key",    "",       "AssemblyAI API Key",   "assemblyai.com",                                         "voice",   True),
]

CATEGORIES = {
    "core":     ("⚙",  "Core AI",            "Main engine, router, trust, dashboard"),
    "telegram": ("📱",  "Telegram",           "Bot token, chat ID, trust level"),
    "image":    ("🎨",  "Image Generation",   "FAL.ai, DALL-E, Stability AI, Replicate"),
    "video":    ("🎬",  "Video Generation",   "RunwayML, Kling, Pika, Luma"),
    "voice":    ("🔊",  "Voice / TTS / STT",  "ElevenLabs, OpenAI TTS, PlayHT, Deepgram"),
}


def get(key: str, fallback: str = "") -> str:
    return get_config(key, fallback)


def set(key: str, value: str):
    save_config(key, value)


def get_category(cat: str) -> list:
    """Return all settings for a given category."""
    return [s for s in SETTINGS if s[4] == cat]


def get_status_summary() -> dict:
    """Returns a summary of which settings are configured."""
    result = {}
    for cat, (icon, label, _) in CATEGORIES.items():
        items   = get_category(cat)
        secrets = [s for s in items if s[5]]
        configured = sum(1 for s in secrets if get(s[0]))
        total      = len(secrets)
        result[cat] = {
            "icon":       icon,
            "label":      label,
            "configured": configured,
            "total":      total,
            "done":       configured == total and total > 0,
        }
    return result
