"""Tests for the first-run setup wizard and default-provider resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from phantom.config.providers import CustomProvider, ProviderRegistry


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path


def _scripted_io():
    inputs: list[str] = []
    outputs: list[str] = []

    def read_line(_prompt: str) -> str:
        return inputs.pop(0)

    def write(s: str) -> None:
        outputs.append(s)

    return inputs, outputs, read_line, write


# ─── ProviderRegistry default-name semantics ─────────────────────────────────

def test_first_add_becomes_default_automatically(home: Path):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a", model="m"))
    assert reg.default_name == "alpha"
    assert reg.get_default().name == "alpha"


def test_subsequent_adds_do_not_override_default(home: Path):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a", model="m"))
    reg.add(CustomProvider(name="beta", base_url="https://b", model="m"))
    assert reg.default_name == "alpha"


def test_set_default_persists_across_load(home: Path):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a", model="m"))
    reg.add(CustomProvider(name="beta", base_url="https://b", model="m"))
    reg.set_default("beta")
    again = ProviderRegistry.load()
    assert again.default_name == "beta"
    assert again.get_default().name == "beta"


def test_set_default_rejects_unknown(home: Path):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a", model="m"))
    with pytest.raises(ValueError, match="unknown provider"):
        reg.set_default("ghost")


def test_remove_default_promotes_first_remaining(home: Path):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a", model="m"))
    reg.add(CustomProvider(name="beta", base_url="https://b", model="m"))
    reg.set_default("beta")
    reg.remove("beta")
    assert reg.default_name == "alpha"


def test_remove_only_provider_clears_default(home: Path):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a", model="m"))
    reg.remove("alpha")
    assert reg.default_name == ""
    assert reg.get_default() is None


def test_load_repairs_dangling_default_pointer(home: Path, tmp_path: Path):
    """If providers.json names a default that doesn't exist (manual edit, race),
    load() should drop the stale pointer rather than crash later."""
    p = tmp_path / "providers.json"
    p.write_text('{"default": "ghost", "custom": {"alpha": {"base_url": "https://a", "model": "m"}}}')
    reg = ProviderRegistry.load(p)
    assert reg.default_name == ""


# ─── setup_wizard.run_wizard ─────────────────────────────────────────────────

def test_wizard_picks_preset_by_number_and_saves_default(home: Path):
    from phantom.cli.setup_wizard import _ordered_presets, run_wizard
    presets = _ordered_presets()
    nvidia_idx = next(i for i, p in enumerate(presets, start=1) if p.name == "nvidia")
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend([str(nvidia_idx), "fake-key", ""])  # pick, key, keep-default-model
    result = run_wizard(read_line=read_line, write=write)
    assert not result.cancelled
    assert result.provider.name == "nvidia"
    again = ProviderRegistry.load()
    assert again.default_name == "nvidia"
    assert again.get("nvidia").api_key_inline == "fake-key"


def test_wizard_picks_preset_by_name(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend(["groq", "g-key", ""])
    result = run_wizard(read_line=read_line, write=write)
    assert not result.cancelled
    assert result.provider.name == "groq"


def test_wizard_cancel_returns_cancelled(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.append("q")
    result = run_wizard(read_line=read_line, write=write)
    assert result.cancelled
    assert result.provider is None
    # Nothing saved.
    assert ProviderRegistry.load().default_name == ""


def test_wizard_local_preset_does_not_require_key(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend(["ollama", ""])  # name, then keep-default-model — no key prompt
    result = run_wizard(read_line=read_line, write=write)
    assert not result.cancelled
    assert result.provider.name == "ollama"


def test_wizard_custom_provider(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend(["custom", "myhost", "https://my.host/v1", "my-model", "k123"])
    result = run_wizard(read_line=read_line, write=write)
    assert not result.cancelled
    assert result.provider.name == "myhost"
    assert ProviderRegistry.load().default_name == "myhost"


# ─── resolve_chat_config ─────────────────────────────────────────────────────

def test_resolve_uses_explicit_flags_first(home: Path, monkeypatch):
    from phantom.cli.chat import resolve_chat_config
    base, key, model, default = resolve_chat_config(
        base_url="https://x", api_key="k", model="m",
    )
    assert (base, key, model, default) == ("https://x", "k", "m", None)


def test_resolve_falls_back_to_default_provider(home: Path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret")
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(
        name="myp", base_url="https://x", model="m", api_key_env="MY_KEY",
    ))
    from phantom.cli.chat import resolve_chat_config
    base, key, model, default = resolve_chat_config(
        base_url="", api_key="", model="",
    )
    assert (base, key, model) == ("https://x", "secret", "m")
    assert default.name == "myp"


def test_resolve_returns_empty_when_nothing_configured(home: Path):
    from phantom.cli.chat import resolve_chat_config
    base, key, model, default = resolve_chat_config(
        base_url="", api_key="", model="",
    )
    assert (base, model, default) == ("", "", None)


def test_should_run_wizard_skips_when_explicit_flags(home: Path):
    from phantom.cli.setup_wizard import should_run_wizard
    assert should_run_wizard(base_url="https://x", model="m") is False


def test_should_run_wizard_skips_when_default_saved(home: Path):
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="alpha", base_url="https://a", model="m"))
    from phantom.cli.setup_wizard import should_run_wizard
    assert should_run_wizard(base_url="", model="") is False


def test_should_run_wizard_runs_on_clean_install(home: Path):
    from phantom.cli.setup_wizard import should_run_wizard
    assert should_run_wizard(base_url="", model="") is True
