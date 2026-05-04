"""Curated provider presets.

A "preset" is a one-line shortcut that pre-fills the OpenAI-compatible
``base_url`` + a sensible default ``model`` + the env-var name where
the user keeps the key. Saves the operator from looking up the
endpoint URL every time.

Adding a new preset is one tuple — keep this list short and well-tested
rather than long and stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

__all__ = ["Preset", "PRESETS", "get_preset", "list_presets"]


@dataclass(frozen=True, slots=True)
class Preset:
    name: str
    base_url: str
    model: str           # sensible default — operator can override
    api_key_env: str
    homepage: str = ""


# All presets are OpenAI-chat-compatible. Operator can override the model
# via --model on `phantom config provider preset <name> --model <m>`.
PRESETS: tuple[Preset, ...] = (
    Preset("together",   "https://api.together.xyz/v1",
           "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY",
           "https://www.together.ai"),
    Preset("fireworks",  "https://api.fireworks.ai/inference/v1",
           "accounts/fireworks/models/llama-v3p3-70b-instruct", "FIREWORKS_API_KEY",
           "https://fireworks.ai"),
    Preset("deepinfra",  "https://api.deepinfra.com/v1/openai",
           "meta-llama/Llama-3.3-70B-Instruct", "DEEPINFRA_API_KEY",
           "https://deepinfra.com"),
    Preset("perplexity", "https://api.perplexity.ai",
           "llama-3.1-sonar-large-128k-online", "PERPLEXITY_API_KEY",
           "https://perplexity.ai"),
    Preset("mistral",    "https://api.mistral.ai/v1",
           "mistral-large-latest", "MISTRAL_API_KEY",
           "https://mistral.ai"),
    Preset("groq",       "https://api.groq.com/openai/v1",
           "llama-3.3-70b-versatile", "GROQ_API_KEY",
           "https://groq.com"),
    Preset("nvidia",     "https://integrate.api.nvidia.com/v1",
           "meta/llama-3.3-70b-instruct", "NVIDIA_API_KEY",
           "https://build.nvidia.com"),
    Preset("openrouter", "https://openrouter.ai/api/v1",
           "anthropic/claude-3.5-sonnet", "OPENROUTER_API_KEY",
           "https://openrouter.ai"),
    Preset("deepseek",   "https://api.deepseek.com",
           "deepseek-chat", "DEEPSEEK_API_KEY",
           "https://deepseek.com"),
    Preset("ollama",     "http://localhost:11434/v1",
           "llama3.3", "OLLAMA_API_KEY",
           "https://ollama.com"),
    Preset("lmstudio",   "http://localhost:1234/v1",
           "local-model", "LMSTUDIO_API_KEY",
           "https://lmstudio.ai"),
    Preset("cerebras",   "https://api.cerebras.ai/v1",
           "llama-3.3-70b", "CEREBRAS_API_KEY",
           "https://cerebras.ai"),
    Preset("xai",        "https://api.x.ai/v1",
           "grok-2-latest", "XAI_API_KEY",
           "https://x.ai"),
    Preset("github",     "https://models.inference.ai.azure.com",
           "gpt-4o", "GITHUB_TOKEN",
           "https://github.com/marketplace/models"),
    Preset("vllm-local", "http://localhost:8000/v1",
           "meta-llama/Llama-3.3-70B-Instruct", "VLLM_API_KEY",
           "https://docs.vllm.ai"),
)


def get_preset(name: str) -> Preset | None:
    name_lower = name.lower().strip()
    for p in PRESETS:
        if p.name == name_lower:
            return p
    return None


def list_presets() -> Iterable[Preset]:
    return PRESETS
