"""Tests for custom OpenAI-compatible provider registry."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from phantom.config.providers import (
    CustomProvider,
    ProviderRegistry,
    providers_path,
)


@pytest.fixture
def registry_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path / "providers.json"


def test_validate_rejects_bad_name():
    with pytest.raises(ValueError):
        CustomProvider(name="X bad", base_url="https://x.com", model="m").validate()


def test_validate_requires_https_base_url():
    with pytest.raises(ValueError):
        CustomProvider(name="x", base_url="ftp://x", model="m").validate()


def test_validate_requires_model():
    with pytest.raises(ValueError):
        CustomProvider(name="x", base_url="https://x", model="").validate()


def test_add_and_list(registry_path: Path):
    reg = ProviderRegistry.load(registry_path)
    reg.add(CustomProvider(name="vllm-local", base_url="http://localhost:8000", model="llama-3.3-70b"))
    reg2 = ProviderRegistry.load(registry_path)
    items = reg2.list()
    assert len(items) == 1
    assert items[0].name == "vllm-local"
    assert items[0].model == "llama-3.3-70b"


def test_add_refuses_overwrite_unless_forced(registry_path: Path):
    reg = ProviderRegistry.load(registry_path)
    reg.add(CustomProvider(name="x", base_url="https://x", model="m"))
    with pytest.raises(ValueError, match="already exists"):
        reg.add(CustomProvider(name="x", base_url="https://y", model="n"))
    reg.add(CustomProvider(name="x", base_url="https://y", model="n"), overwrite=True)
    assert reg.get("x").base_url == "https://y"


def test_remove(registry_path: Path):
    reg = ProviderRegistry.load(registry_path)
    reg.add(CustomProvider(name="x", base_url="https://x", model="m"))
    assert reg.remove("x") is True
    assert reg.remove("x") is False


def test_persisted_file_is_owner_only(registry_path: Path):
    reg = ProviderRegistry.load(registry_path)
    reg.add(CustomProvider(name="x", base_url="https://x", model="m"))
    mode = os.stat(registry_path).st_mode & 0o777
    assert mode == 0o600


def test_persisted_json_shape(registry_path: Path):
    reg = ProviderRegistry.load(registry_path)
    reg.add(CustomProvider(
        name="vllm",
        base_url="http://h:8000",
        model="m",
        api_key_env="VLLM_KEY",
    ))
    body = json.loads(registry_path.read_text())
    assert "custom" in body
    assert body["custom"]["vllm"]["api_key_env"] == "VLLM_KEY"
    assert "name" not in body["custom"]["vllm"]  # name is the dict key


def test_load_with_garbage_returns_empty(tmp_path: Path):
    p = tmp_path / "providers.json"
    p.write_text("not json {")
    reg = ProviderRegistry.load(p)
    assert reg.list() == []
