"""Tests for the curated provider preset registry."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from phantom.config.presets import PRESETS, Preset, get_preset, list_presets


def test_at_least_15_presets_shipped():
    assert len(PRESETS) >= 15


def test_every_preset_has_required_fields():
    for p in PRESETS:
        assert isinstance(p, Preset)
        assert p.name and p.name.isidentifier() or "-" in p.name or "_" in p.name
        assert p.base_url
        assert p.model
        assert p.api_key_env


def test_every_preset_has_valid_https_or_local_base_url():
    for p in PRESETS:
        parsed = urlparse(p.base_url)
        assert parsed.scheme in ("http", "https"), f"{p.name}: bad scheme {parsed.scheme}"
        assert parsed.netloc, f"{p.name}: no host"


def test_preset_names_unique():
    names = [p.name for p in PRESETS]
    assert len(names) == len(set(names))


def test_preset_env_vars_uppercase():
    for p in PRESETS:
        assert p.api_key_env == p.api_key_env.upper(), f"{p.name}: env var not uppercase"


@pytest.mark.parametrize("preset_name", [
    "together", "fireworks", "deepinfra", "perplexity", "mistral",
    "groq", "nvidia", "openrouter", "deepseek", "ollama", "lmstudio",
    "cerebras", "xai", "github", "vllm-local",
])
def test_well_known_presets_are_registered(preset_name):
    p = get_preset(preset_name)
    assert p is not None, f"missing preset: {preset_name}"


def test_get_preset_case_insensitive():
    p = get_preset("TOGETHER")
    assert p is not None and p.name == "together"


def test_get_preset_returns_none_for_unknown():
    assert get_preset("does-not-exist") is None


def test_list_presets_returns_all():
    names = {p.name for p in list_presets()}
    assert {"together", "fireworks", "groq"}.issubset(names)


def test_local_presets_use_localhost():
    for name in ("ollama", "lmstudio", "vllm-local"):
        p = get_preset(name)
        assert p is not None
        assert "localhost" in p.base_url or "127.0.0.1" in p.base_url
