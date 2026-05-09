"""Custom provider registry — add OpenAI-compatible endpoints.

A "custom provider" is anything that speaks OpenAI's chat-completions
wire format: vLLM, Ollama, LM Studio, Together, DeepInfra,
self-hosted gateways. The user supplies a name, base URL, model id,
and optional API key. We persist them in
``$PHANTOM_HOME/providers.json`` so the engine can route to them by
name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CustomProvider",
    "ProviderRegistry",
    "normalize_base_url",
    "providers_path",
]


_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}[a-z0-9]?$")

# Endpoint paths that get auto-stripped from base URLs. The OpenAI shape is
# `<base>/chat/completions`, so users who paste the full URL from a docs page
# end up with `<base>/chat/completions/chat/completions` → 404. Strip the
# longest matching suffix and any trailing slash.
_ENDPOINT_SUFFIXES = (
    "/chat/completions",
    "/completions",
    "/embeddings",
    "/messages",   # Anthropic-shaped paste — still wrong for OpenAI-compat
    "/responses",  # OpenAI Responses API path
)


def normalize_base_url(url: str) -> str:
    """Strip trailing endpoint paths and slashes so the provider can append
    its own ``/chat/completions``.

    `https://api.example.com/v1/chat/completions` → `https://api.example.com/v1`
    `https://api.example.com/v1/`                 → `https://api.example.com/v1`
    `https://api.example.com/v1`                  → unchanged
    """
    cleaned = url.strip().rstrip("/")
    # Sort by length so the longest suffix wins.
    for suffix in sorted(_ENDPOINT_SUFFIXES, key=len, reverse=True):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned.rstrip("/")


def providers_path() -> Path:
    base = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom"))
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / "providers.json"


@dataclass(frozen=True, slots=True)
class CustomProvider:
    name: str
    base_url: str
    model: str
    api_key_env: str = ""        # name of env var holding key — never the value
    api_key_inline: str = ""     # for ephemeral keys; persisted, owner-only mode
    extra_headers: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"invalid provider name {self.name!r}: must match [a-z][a-z0-9_-]*[a-z0-9]"
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be http(s)://, got {self.base_url!r}")
        if not self.model:
            raise ValueError("model must not be empty")


@dataclass
class ProviderRegistry:
    path: Path
    _providers: dict[str, CustomProvider]
    _default: str = ""

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ProviderRegistry":
        target = Path(path) if path else providers_path()
        if not target.exists():
            return cls(path=target, _providers={}, _default="")
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls(path=target, _providers={}, _default="")
        providers = {}
        repaired = False
        for name, body in (data.get("custom") or {}).items():
            if not isinstance(body, dict):
                continue
            raw_url = str(body.get("base_url", ""))
            normalized = normalize_base_url(raw_url)
            if normalized != raw_url:
                repaired = True
            try:
                providers[name] = CustomProvider(
                    name=name,
                    base_url=normalized,
                    model=str(body.get("model", "")),
                    api_key_env=str(body.get("api_key_env", "")),
                    api_key_inline=str(body.get("api_key_inline", "")),
                    extra_headers=dict(body.get("extra_headers") or {}),
                )
            except ValueError:
                continue
        default = str(data.get("default") or "")
        if default and default not in providers:
            default = ""
        registry = cls(path=target, _providers=providers, _default=default)
        if repaired:
            registry._save()
        return registry

    def list(self) -> list[CustomProvider]:
        return [self._providers[k] for k in sorted(self._providers)]

    def get(self, name: str) -> CustomProvider | None:
        return self._providers.get(name)

    def add(self, provider: CustomProvider, *, overwrite: bool = False) -> None:
        # Auto-strip /chat/completions etc. — saves users from the
        # paste-from-docs trap that produces double-paths and 404s.
        normalized = normalize_base_url(provider.base_url)
        if normalized != provider.base_url:
            provider = CustomProvider(
                name=provider.name,
                base_url=normalized,
                model=provider.model,
                api_key_env=provider.api_key_env,
                api_key_inline=provider.api_key_inline,
                extra_headers=provider.extra_headers,
            )
        provider.validate()
        if not overwrite and provider.name in self._providers:
            raise ValueError(f"provider {provider.name!r} already exists; pass overwrite=True")
        first = not self._providers
        self._providers[provider.name] = provider
        if first and not self._default:
            self._default = provider.name
        self._save()

    def remove(self, name: str) -> bool:
        if name not in self._providers:
            return False
        del self._providers[name]
        if self._default == name:
            self._default = next(iter(sorted(self._providers)), "")
        self._save()
        return True

    def set_default(self, name: str) -> None:
        if name not in self._providers:
            raise ValueError(f"unknown provider {name!r}")
        self._default = name
        self._save()

    def get_default(self) -> CustomProvider | None:
        if not self._default:
            return None
        return self._providers.get(self._default)

    @property
    def default_name(self) -> str:
        return self._default

    def _save(self) -> None:
        body: dict[str, Any] = {
            "custom": {p.name: _provider_to_dict(p) for p in self.list()},
        }
        if self._default:
            body["default"] = self._default
        out = json.dumps(body, indent=2, sort_keys=True)
        self.path.write_text(out, encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def _provider_to_dict(p: CustomProvider) -> dict[str, Any]:
    out = {
        "base_url": p.base_url,
        "model": p.model,
    }
    if p.api_key_env:
        out["api_key_env"] = p.api_key_env
    if p.api_key_inline:
        out["api_key_inline"] = p.api_key_inline
    if p.extra_headers:
        out["extra_headers"] = dict(p.extra_headers)
    return out
