"""Tests for base_url normalization — strip trailing endpoint paths.

Triggered by the v1.1.8 user report: pasting
``https://integrate.api.nvidia.com/v1/chat/completions`` as the base URL
produced 404s because the provider built ``…/v1/chat/completions/chat/completions``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.config.providers import (
    CustomProvider,
    ProviderRegistry,
    normalize_base_url,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("raw,expected", [
    # The exact paste from the user report.
    ("https://integrate.api.nvidia.com/v1/chat/completions",
     "https://integrate.api.nvidia.com/v1"),
    # Trailing slash gets stripped too.
    ("https://api.example.com/v1/chat/completions/",
     "https://api.example.com/v1"),
    # Already clean — unchanged.
    ("https://api.example.com/v1",
     "https://api.example.com/v1"),
    ("https://api.example.com/v1/",
     "https://api.example.com/v1"),
    # Other OpenAI sub-endpoints that also need stripping.
    ("https://api.example.com/v1/embeddings",
     "https://api.example.com/v1"),
    ("https://api.example.com/v1/responses",
     "https://api.example.com/v1"),
    # Anthropic-shaped paste — wrong for OpenAI-compat anyway, but the
    # path stripper still leaves a sensible base.
    ("https://api.anthropic.com/v1/messages",
     "https://api.anthropic.com/v1"),
    # Local endpoint with /v1/chat/completions appended.
    ("http://localhost:8000/v1/chat/completions",
     "http://localhost:8000/v1"),
    # Just /completions (no /chat prefix).
    ("https://api.example.com/v1/completions",
     "https://api.example.com/v1"),
    # No path at all — leave alone.
    ("https://api.example.com",
     "https://api.example.com"),
    # Whitespace.
    ("  https://api.example.com/v1/chat/completions  ",
     "https://api.example.com/v1"),
])
def test_normalize_base_url_table(raw: str, expected: str):
    assert normalize_base_url(raw) == expected


def test_add_strips_chat_completions_suffix(home: Path):
    """The exact bug: user pastes /v1/chat/completions, registry saves /v1."""
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(
        name="nvidia2",
        base_url="https://integrate.api.nvidia.com/v1/chat/completions",
        model="moonshotai/kimi-k2.6",
    ))
    p = reg.get("nvidia2")
    assert p is not None
    assert p.base_url == "https://integrate.api.nvidia.com/v1"


def test_load_repairs_existing_bad_entry(home: Path, tmp_path: Path):
    """If providers.json on disk has a bad URL (saved before this fix),
    loading it should silently rewrite the file with the cleaned URL."""
    p = tmp_path / "providers.json"
    p.write_text(json.dumps({
        "default": "nvidia2",
        "custom": {
            "nvidia2": {
                "base_url": "https://integrate.api.nvidia.com/v1/chat/completions",
                "model": "moonshotai/kimi-k2.6",
            },
        },
    }))
    reg = ProviderRegistry.load(p)
    saved = reg.get("nvidia2")
    assert saved.base_url == "https://integrate.api.nvidia.com/v1"
    # And the file on disk was rewritten.
    persisted = json.loads(p.read_text())
    assert persisted["custom"]["nvidia2"]["base_url"] == "https://integrate.api.nvidia.com/v1"


def test_load_does_not_rewrite_when_all_clean(home: Path, tmp_path: Path):
    """Healthy file: load() must not write to disk (no mtime churn)."""
    p = tmp_path / "providers.json"
    body = json.dumps({
        "default": "ok",
        "custom": {"ok": {"base_url": "https://x.test/v1", "model": "m"}},
    })
    p.write_text(body)
    mtime_before = p.stat().st_mtime_ns
    ProviderRegistry.load(p)
    mtime_after = p.stat().st_mtime_ns
    assert mtime_before == mtime_after
