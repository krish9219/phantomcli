"""First-run setup wizard for ``phantom chat``.

When the user runs ``phantom chat`` (or any flow that needs a provider)
without ``--base-url`` / ``--model`` / matching env vars and with no
default provider already saved, we drop into this wizard. It lists all
presets, asks the user to pick one, asks for an API key (or reuses the
preset's env var), and saves the choice as the default provider.

No baked-in defaults: every install picks its own. Subsequent runs of
``phantom chat`` skip the wizard and go straight to the saved default.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable

from phantom.config.presets import PRESETS, Preset
from phantom.config.providers import CustomProvider, ProviderRegistry

__all__ = ["WizardResult", "run_wizard", "should_run_wizard"]


@dataclass(frozen=True, slots=True)
class WizardResult:
    """What the wizard saved. ``cancelled`` if the user bailed."""
    provider: CustomProvider | None
    cancelled: bool = False


# Presets ordered so free + popular options come first.
_ORDERED = [
    "nvidia", "groq", "openrouter", "github", "deepseek",
    "together", "fireworks", "mistral", "cerebras", "perplexity",
    "deepinfra", "xai", "ollama", "lmstudio", "vllm-local",
]


def _ordered_presets() -> list[Preset]:
    by_name = {p.name: p for p in PRESETS}
    out: list[Preset] = []
    for n in _ORDERED:
        if n in by_name:
            out.append(by_name[n])
    for p in PRESETS:
        if p.name not in _ORDERED:
            out.append(p)
    return out


def should_run_wizard(
    *, base_url: str, model: str, registry: ProviderRegistry | None = None,
) -> bool:
    """True iff chat has nothing to use.

    If the user passed flags / set env vars, honour those — never override
    explicit user choice. If a default provider is already saved, use it.
    Otherwise the wizard runs.
    """
    if base_url and model:
        return False
    if registry is None:
        registry = ProviderRegistry.load()
    return registry.get_default() is None


def run_wizard(
    *,
    read_line: Callable[[str], str] | None = None,
    write: Callable[[str], None] | None = None,
    registry: ProviderRegistry | None = None,
) -> WizardResult:
    """Drive the picker. Returns the saved CustomProvider or cancelled.

    ``read_line`` and ``write`` default to the standard streams; tests
    pass deterministic substitutes.
    """
    if read_line is None:
        def _r(prompt: str) -> str:
            return input(prompt)
        read_line = _r
    if write is None:
        def _w(s: str) -> None:
            sys.stdout.write(s)
            sys.stdout.flush()
        write = _w

    if registry is None:
        registry = ProviderRegistry.load()

    presets = _ordered_presets()

    write("\n  Phantom — first-run setup\n")
    write("  ─────────────────────────\n")
    write("  Pick a provider for `phantom chat`. You can change this later with\n")
    write("  `phantom config provider use <name>` or add more with\n")
    write("  `phantom config provider preset <name>`.\n\n")
    for i, p in enumerate(presets, start=1):
        free_hint = ""
        if p.name in ("ollama", "lmstudio", "vllm-local"):
            free_hint = "  (local, no key)"
        elif p.name in ("nvidia", "groq", "github", "openrouter"):
            free_hint = "  (free tier)"
        write(f"   {i:>2}) {p.name:<11} {p.model:<48}{free_hint}\n")
    custom_idx = len(presets) + 1
    write(f"   {custom_idx:>2}) custom      any OpenAI-compatible base URL\n")
    write("    q) cancel\n\n")

    choice = read_line("  pick> ").strip().lower()
    if choice in {"", "q", "quit", "exit", "cancel"}:
        write("  (cancelled)\n")
        return WizardResult(provider=None, cancelled=True)

    if choice == "custom" or (choice.isdigit() and int(choice) == custom_idx):
        return _wizard_custom(read_line, write, registry)

    if not choice.isdigit():
        for p in presets:
            if p.name == choice:
                return _wizard_preset(p, read_line, write, registry)
        write(f"  unknown choice {choice!r}\n")
        return WizardResult(provider=None, cancelled=True)

    idx = int(choice)
    if not 1 <= idx <= len(presets):
        write(f"  out of range: {idx}\n")
        return WizardResult(provider=None, cancelled=True)

    return _wizard_preset(presets[idx - 1], read_line, write, registry)


def _wizard_preset(
    preset: Preset,
    read_line: Callable[[str], str],
    write: Callable[[str], None],
    registry: ProviderRegistry,
) -> WizardResult:
    write(f"\n  {preset.name} → {preset.base_url}\n")
    write(f"  default model: {preset.model}\n")
    if preset.homepage:
        write(f"  homepage:      {preset.homepage}\n")

    api_key_env = preset.api_key_env
    api_key_inline = ""

    needs_key = preset.name not in ("ollama", "lmstudio", "vllm-local")
    if needs_key:
        existing = os.environ.get(api_key_env, "")
        if existing:
            write(f"  using {api_key_env} from environment.\n")
        else:
            write(f"  paste your API key (or press Enter to set ${api_key_env} later):\n")
            try:
                key = read_line("  key> ").strip()
            except EOFError:
                key = ""
            if key:
                api_key_inline = key

    model = preset.model
    custom_model = read_line(
        f"  model [{preset.model}] (Enter to keep): "
    ).strip()
    if custom_model:
        model = custom_model

    provider = CustomProvider(
        name=preset.name,
        base_url=preset.base_url,
        model=model,
        api_key_env=api_key_env,
        api_key_inline=api_key_inline,
    )
    try:
        registry.add(provider, overwrite=True)
        registry.set_default(preset.name)
    except ValueError as e:
        write(f"  failed: {e}\n")
        return WizardResult(provider=None, cancelled=True)

    write(f"\n  saved as default: {preset.name}\n")
    if needs_key and not api_key_inline and not os.environ.get(api_key_env, ""):
        write(f"  reminder: export {api_key_env}=<your key> before chatting.\n")
    return WizardResult(provider=provider)


def _wizard_custom(
    read_line: Callable[[str], str],
    write: Callable[[str], None],
    registry: ProviderRegistry,
) -> WizardResult:
    write("\n  custom OpenAI-compatible provider\n")
    name = read_line("  short name (a-z, dashes ok): ").strip().lower()
    base_url = read_line("  base URL (https://…): ").strip()
    model = read_line("  model id: ").strip()
    write("  paste an API key (Enter to skip — local endpoints don't need one):\n")
    try:
        key = read_line("  key> ").strip()
    except EOFError:
        key = ""

    try:
        provider = CustomProvider(
            name=name, base_url=base_url, model=model, api_key_inline=key,
        )
        registry.add(provider, overwrite=True)
        registry.set_default(name)
    except ValueError as e:
        write(f"  failed: {e}\n")
        return WizardResult(provider=None, cancelled=True)

    write(f"\n  saved as default: {name}\n")
    return WizardResult(provider=provider)
