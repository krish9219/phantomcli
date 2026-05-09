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


# ─── setup_wizard.run_wizard (3-prompt custom flow) ──────────────────────────

def test_wizard_three_prompts_save_provider(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend([
        "https://integrate.api.nvidia.com/v1",
        "meta/llama-3.3-70b-instruct",
        "nv-secret",
    ])
    result = run_wizard(read_line=read_line, write=write)
    assert not result.cancelled
    p = result.provider
    assert p.base_url == "https://integrate.api.nvidia.com/v1"
    assert p.model == "meta/llama-3.3-70b-instruct"
    assert p.api_key_inline == "nv-secret"
    again = ProviderRegistry.load()
    assert again.default_name == p.name
    assert again.get(p.name) == p


def test_wizard_blank_base_url_cancels(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.append("")  # blank base URL → cancel
    result = run_wizard(read_line=read_line, write=write)
    assert result.cancelled
    assert ProviderRegistry.load().default_name == ""


def test_wizard_rejects_non_http_base_url(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.append("ftp://x")
    result = run_wizard(read_line=read_line, write=write)
    assert result.cancelled
    assert ProviderRegistry.load().default_name == ""


def test_wizard_blank_model_cancels(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend(["https://x.test/v1", ""])
    result = run_wizard(read_line=read_line, write=write)
    assert result.cancelled
    assert ProviderRegistry.load().default_name == ""


def test_wizard_blank_key_is_allowed_for_local_endpoints(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend(["http://localhost:11434/v1", "llama3.3", ""])
    result = run_wizard(read_line=read_line, write=write)
    assert not result.cancelled
    assert result.provider.api_key_inline == ""


def test_wizard_derives_name_from_hostname(home: Path):
    from phantom.cli.setup_wizard import run_wizard
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend(["https://api.together.xyz/v1", "m", "k"])
    result = run_wizard(read_line=read_line, write=write)
    assert not result.cancelled
    assert result.provider.name == "together"


def test_wizard_appends_suffix_when_name_taken(home: Path):
    """If `together` already exists, the wizard saves the new entry as
    `together-2` (and so on) rather than overwriting silently."""
    from phantom.cli.setup_wizard import run_wizard
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="together", base_url="https://a.test", model="x"))
    inputs, _outputs, read_line, write = _scripted_io()
    inputs.extend(["https://api.together.xyz/v1", "m", "k"])
    result = run_wizard(read_line=read_line, write=write)
    assert not result.cancelled
    assert result.provider.name == "together-2"


# ─── derive_name unit ────────────────────────────────────────────────────────

def test_derive_name_known_hosts(home: Path):
    from phantom.cli.setup_wizard import derive_name
    reg = ProviderRegistry.load()
    assert derive_name("https://api.together.xyz/v1", reg) == "together"
    assert derive_name("https://integrate.api.nvidia.com/v1", reg) == "nvidia"
    assert derive_name("https://api.groq.com/openai/v1", reg) == "groq"
    assert derive_name("https://models.github.ai/inference", reg) == "github"
    assert derive_name("https://api.openai.com/v1", reg) == "openai"
    assert derive_name("http://localhost:11434/v1", reg) == "localhost"
    assert derive_name("not-a-url", reg) == "default"


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
