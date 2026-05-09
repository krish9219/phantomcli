"""``phantom config provider ...`` — manage custom OpenAI-compatible endpoints."""

from __future__ import annotations

import json
from typing import Optional

import typer

from phantom.config.providers import (
    CustomProvider,
    ProviderRegistry,
)

__all__ = ["config_app"]


config_app = typer.Typer(
    name="config",
    help="Configure providers, models, and runtime defaults.",
    no_args_is_help=True,
)
provider_app = typer.Typer(
    name="provider",
    help="Manage custom OpenAI-compatible providers.",
    no_args_is_help=True,
)
config_app.add_typer(provider_app, name="provider")


@config_app.command("setup", help="Interactive first-run wizard — pick + save a default provider.")
def setup_cmd() -> None:
    """Run the same wizard `phantom chat` shows on a clean install."""
    from phantom.cli.setup_wizard import run_wizard
    result = run_wizard()
    if result.cancelled:
        raise typer.Exit(2)


@provider_app.command("custom", help="Add (or overwrite) a custom OpenAI-compatible provider.")
def add_custom(
    name: str = typer.Argument(..., help="lowercase identifier, e.g. 'vllm-local'"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="https://… (no trailing /v1 needed)"),
    model: Optional[str] = typer.Option(None, "--model", help="model id the endpoint expects"),
    key_env: Optional[str] = typer.Option(None, "--key-env", help="env var name holding the API key"),
    key: Optional[str] = typer.Option(None, "--key", help="inline API key (stored owner-only)"),
    overwrite: bool = typer.Option(False, "--force", help="overwrite an existing entry"),
) -> None:
    """Register a custom provider. Prompts for any missing flag interactively."""
    if not base_url:
        base_url = typer.prompt("  base URL (https://…)").strip()
    if not model:
        model = typer.prompt("  model id (e.g. gpt-4o, meta/llama-3.3-70b-instruct)").strip()
    if not key and not key_env:
        entered = typer.prompt(
            "  API key (Enter to skip if local / no key needed)",
            default="", show_default=False,
        ).strip()
        if entered:
            key = entered

    registry = ProviderRegistry.load()
    provider = CustomProvider(
        name=name,
        base_url=base_url,
        model=model,
        api_key_env=key_env or "",
        api_key_inline=key or "",
    )
    try:
        registry.add(provider, overwrite=overwrite)
    except ValueError as e:
        typer.echo(f"failed: {e}", err=True)
        raise typer.Exit(2)
    typer.echo(f"  added provider: {name}  → {base_url}  (model={model})")
    if registry.default_name == name:
        typer.echo(f"  default provider: {name}")
    else:
        typer.echo(f"  set as default with: phantom config provider use {name}")


@provider_app.command("preset", help="Add a pre-configured provider in one command (Together, Fireworks, Mistral, …).")
def add_preset(
    name: str = typer.Argument(..., help="preset name; see `phantom config provider presets`"),
    model: Optional[str] = typer.Option(None, "--model", help="override the preset's default model"),
    key_env: Optional[str] = typer.Option(None, "--key-env", help="override the env var name"),
    key: Optional[str] = typer.Option(None, "--key", help="paste the API key inline (stored owner-only)"),
    overwrite: bool = typer.Option(False, "--force", help="overwrite an existing entry with the same name"),
) -> None:
    """Register a popular OpenAI-compatible provider via its preset.

    If neither --key nor an existing env var is found, prompt for the key
    interactively (skipped for local-only presets like ollama / lmstudio).
    """
    import os as _os
    from phantom.config.presets import get_preset

    preset = get_preset(name)
    if preset is None:
        typer.echo(f"unknown preset: {name!r}. run `phantom config provider presets` to list", err=True)
        raise typer.Exit(2)

    api_key_env = key_env or preset.api_key_env
    api_key_inline = key or ""
    if (
        preset.name not in ("ollama", "lmstudio", "vllm-local")
        and not api_key_inline
        and not _os.environ.get(api_key_env, "")
    ):
        entered = typer.prompt(
            f"  API key for {preset.name} (Enter to set ${api_key_env} later)",
            default="", show_default=False,
        ).strip()
        if entered:
            api_key_inline = entered

    registry = ProviderRegistry.load()
    provider = CustomProvider(
        name=preset.name,
        base_url=preset.base_url,
        model=model or preset.model,
        api_key_env=api_key_env,
        api_key_inline=api_key_inline,
    )
    try:
        registry.add(provider, overwrite=overwrite)
    except ValueError as e:
        typer.echo(f"failed: {e}", err=True)
        raise typer.Exit(2)
    typer.echo(f"  added preset: {preset.name}  → {preset.base_url}  (model={provider.model}, key=${provider.api_key_env})")
    typer.echo(f"  homepage: {preset.homepage}")
    if registry.default_name == preset.name:
        typer.echo(f"  default provider: {preset.name}")
    else:
        typer.echo(f"  set as default with: phantom config provider use {preset.name}")


@provider_app.command("presets", help="List available provider presets.")
def list_provider_presets() -> None:
    from phantom.config.presets import list_presets

    typer.echo(f"  {'NAME':<14}{'KEY ENV':<24}{'DEFAULT MODEL'}")
    for p in list_presets():
        typer.echo(f"  {p.name:<14}{p.api_key_env:<24}{p.model}")


@provider_app.command("list", help="List custom providers (* = default).")
def list_custom(json_output: bool = typer.Option(False, "--json")) -> None:
    registry = ProviderRegistry.load()
    rows = registry.list()
    default = registry.default_name
    if json_output:
        typer.echo(json.dumps(
            {
                "default": default,
                "providers": [
                    {"name": p.name, "base_url": p.base_url, "model": p.model, "key_env": p.api_key_env}
                    for p in rows
                ],
            },
            indent=2,
        ))
        return
    if not rows:
        typer.echo("  (no providers configured — run `phantom chat` to set one up)")
        return
    for p in rows:
        key_hint = f"env={p.api_key_env}" if p.api_key_env else ("inline" if p.api_key_inline else "no-key")
        marker = "*" if p.name == default else " "
        typer.echo(f"  {marker} {p.name:<20} {p.base_url}  ({p.model}, {key_hint})")
    if default:
        typer.echo("")
        typer.echo(f"  default: {default}   (change with: phantom config provider use <name>)")


@provider_app.command("use", help="Set the default provider used by `phantom chat`.")
def use_default(name: str = typer.Argument(..., help="provider name (must already be registered)")) -> None:
    registry = ProviderRegistry.load()
    try:
        registry.set_default(name)
    except ValueError as e:
        typer.echo(f"  {e}", err=True)
        names = ", ".join(p.name for p in registry.list()) or "(none)"
        typer.echo(f"  registered providers: {names}", err=True)
        raise typer.Exit(2)
    typer.echo(f"  default provider: {name}")


@provider_app.command("remove", help="Remove a custom provider.")
def remove_custom(name: str = typer.Argument(...)) -> None:
    registry = ProviderRegistry.load()
    if registry.remove(name):
        typer.echo(f"  removed: {name}")
    else:
        typer.echo(f"  no such provider: {name}", err=True)
        raise typer.Exit(1)
