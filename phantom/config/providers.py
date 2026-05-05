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
    "providers_path",
]


_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}[a-z0-9]?$")


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

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ProviderRegistry":
        target = Path(path) if path else providers_path()
        if not target.exists():
            return cls(path=target, _providers={})
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls(path=target, _providers={})
        providers = {}
        for name, body in (data.get("custom") or {}).items():
            if not isinstance(body, dict):
                continue
            try:
                providers[name] = CustomProvider(
                    name=name,
                    base_url=str(body.get("base_url", "")),
                    model=str(body.get("model", "")),
                    api_key_env=str(body.get("api_key_env", "")),
                    api_key_inline=str(body.get("api_key_inline", "")),
                    extra_headers=dict(body.get("extra_headers") or {}),
                )
            except ValueError:
                continue
        return cls(path=target, _providers=providers)

    def list(self) -> list[CustomProvider]:
        return [self._providers[k] for k in sorted(self._providers)]

    def get(self, name: str) -> CustomProvider | None:
        return self._providers.get(name)

    def add(self, provider: CustomProvider, *, overwrite: bool = False) -> None:
        provider.validate()
        if not overwrite and provider.name in self._providers:
            raise ValueError(f"provider {provider.name!r} already exists; pass overwrite=True")
        self._providers[provider.name] = provider
        self._save()

    def remove(self, name: str) -> bool:
        if name not in self._providers:
            return False
        del self._providers[name]
        self._save()
        return True

    def _save(self) -> None:
        body: dict[str, Any] = {"custom": {p.name: _provider_to_dict(p) for p in self.list()}}
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
