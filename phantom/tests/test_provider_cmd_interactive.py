"""Tests for the interactive prompts on `phantom config provider {custom,preset}`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from phantom.cli import app
from phantom.config.providers import ProviderRegistry


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    # Wipe any preset env keys so the prompt actually fires.
    for k in ("NVIDIA_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_provider_custom_prompts_for_missing_url_model_key(home):
    runner = CliRunner()
    # Three answers in order: base_url, model, key.
    result = runner.invoke(
        app,
        ["config", "provider", "custom", "myhost"],
        input="https://my.host/v1\nmy-model\nsk-test\n",
    )
    assert result.exit_code == 0, result.output
    reg = ProviderRegistry.load()
    p = reg.get("myhost")
    assert p is not None
    assert p.base_url == "https://my.host/v1"
    assert p.model == "my-model"
    assert p.api_key_inline == "sk-test"
    assert reg.default_name == "myhost"


def test_provider_custom_skips_key_when_left_blank(home):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["config", "provider", "custom", "local"],
        input="http://localhost:8000/v1\nllama-3\n\n",
    )
    assert result.exit_code == 0, result.output
    p = ProviderRegistry.load().get("local")
    assert p is not None
    assert p.api_key_inline == ""
    assert p.api_key_env == ""


def test_provider_custom_explicit_flags_skip_prompts(home):
    runner = CliRunner()
    # Pass everything as flags — no input needed; if a prompt fires the
    # CliRunner would block.
    result = runner.invoke(
        app,
        [
            "config", "provider", "custom", "explicit",
            "--base-url", "https://x", "--model", "m", "--key", "k",
        ],
        input="",
    )
    assert result.exit_code == 0, result.output
    assert ProviderRegistry.load().get("explicit").api_key_inline == "k"


def test_provider_preset_prompts_for_missing_key(home):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["config", "provider", "preset", "groq"],
        input="gsk_test\n",
    )
    assert result.exit_code == 0, result.output
    p = ProviderRegistry.load().get("groq")
    assert p is not None
    assert p.api_key_inline == "gsk_test"
    assert p.api_key_env == "GROQ_API_KEY"


def test_provider_preset_skips_prompt_when_env_var_set(home, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "in-env")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["config", "provider", "preset", "groq"],
        input="",  # no input — if a prompt fires it'll EOFError
    )
    assert result.exit_code == 0, result.output
    p = ProviderRegistry.load().get("groq")
    assert p.api_key_inline == ""  # env var, not stored inline


def test_provider_preset_skips_prompt_for_local_only(home):
    """ollama / lmstudio / vllm-local don't need a key."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["config", "provider", "preset", "ollama"],
        input="",
    )
    assert result.exit_code == 0, result.output
    assert ProviderRegistry.load().get("ollama") is not None


def test_config_setup_runs_wizard(home):
    runner = CliRunner()
    # New 3-prompt flow: base URL, model, API key.
    result = runner.invoke(
        app, ["config", "setup"],
        input="https://api.test/v1\ntest-model\nk\n",
    )
    assert result.exit_code == 0, result.output
    reg = ProviderRegistry.load()
    assert reg.default_name != ""


def test_config_setup_cancel_exits_2(home):
    runner = CliRunner()
    # Blank base URL cancels.
    result = runner.invoke(app, ["config", "setup"], input="\n")
    assert result.exit_code == 2
    assert ProviderRegistry.load().default_name == ""
