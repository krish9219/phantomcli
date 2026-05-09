"""First-run setup wizard for ``phantom chat``.

The wizard is a direct 3-prompt flow: paste a base URL, model id, and
API key for any OpenAI-compatible endpoint. Phantom doesn't ship a
default provider — every install picks its own — and once you've
configured one, subsequent ``phantom chat`` runs skip the wizard.

Examples of OpenAI-compatible endpoints that work here:

* NVIDIA NIM     → ``https://integrate.api.nvidia.com/v1``
* Groq           → ``https://api.groq.com/openai/v1``
* OpenRouter     → ``https://openrouter.ai/api/v1``
* Together       → ``https://api.together.xyz/v1``
* Fireworks      → ``https://api.fireworks.ai/inference/v1``
* GitHub Models  → ``https://models.github.ai/inference``
* Ollama         → ``http://localhost:11434/v1``  (local, no key)
* vLLM / LM Studio → any ``http://…/v1`` you serve

For one-line shortcuts to popular providers see
``phantom config provider preset <name>``.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from phantom.config.providers import CustomProvider, ProviderRegistry

__all__ = ["WizardResult", "run_wizard", "should_run_wizard", "derive_name"]


@dataclass(frozen=True, slots=True)
class WizardResult:
    """What the wizard saved. ``cancelled`` if the user bailed."""
    provider: CustomProvider | None
    cancelled: bool = False


_NAME_OK = re.compile(r"^[a-z][a-z0-9_-]{0,30}[a-z0-9]?$")


def derive_name(base_url: str, registry: ProviderRegistry) -> str:
    """Best-effort default name from the base URL's host.

    Picks the registered-domain label (second-to-last for multi-label hosts):

    * ``integrate.api.nvidia.com``  → ``nvidia``
    * ``api.together.xyz``          → ``together``
    * ``models.github.ai``          → ``github``
    * ``localhost``                 → ``localhost``
    * unparseable URL               → ``default``

    Appends ``-2``, ``-3`` etc. if the candidate is already in the registry.
    """
    host = (urlparse(base_url).hostname or "").lower()

    candidate = "default"
    if host:
        labels = host.split(".")
        if len(labels) == 1:
            candidate = labels[0]
        elif len(labels) >= 2:
            candidate = labels[-2]
        cleaned = re.sub(r"[^a-z0-9_-]", "-", candidate).strip("-")
        if cleaned and _NAME_OK.match(cleaned):
            candidate = cleaned
        else:
            candidate = "default"

    if registry.get(candidate) is None:
        return candidate
    for n in range(2, 100):
        suffix = f"{candidate}-{n}"
        if registry.get(suffix) is None:
            return suffix
    return candidate


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
    """Drive the 3-prompt setup. Returns the saved CustomProvider or cancelled.

    ``read_line`` and ``write`` default to the standard streams; tests pass
    deterministic substitutes.
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

    write("\n  Phantom — first-run setup\n")
    write("  ─────────────────────────\n")
    write("  Phantom works with any OpenAI-compatible endpoint.\n")
    write("  Examples: NVIDIA NIM, Groq, OpenRouter, Together, Fireworks,\n")
    write("            GitHub Models, Ollama, vLLM, LM Studio.\n")
    write("  Tip: for one-line shortcuts run `phantom config provider preset <name>`.\n")
    write("  Press Ctrl+C or leave the base URL blank to cancel.\n\n")

    try:
        base_url = read_line("  base URL (https://…/v1)> ").strip()
    except (EOFError, KeyboardInterrupt):
        write("\n  (cancelled)\n")
        return WizardResult(provider=None, cancelled=True)
    if not base_url:
        write("  (cancelled)\n")
        return WizardResult(provider=None, cancelled=True)
    if not base_url.startswith(("http://", "https://")):
        write(f"  base URL must start with http:// or https://, got: {base_url!r}\n")
        return WizardResult(provider=None, cancelled=True)

    try:
        model = read_line("  model id (e.g. gpt-4o, meta/llama-3.3-70b-instruct)> ").strip()
    except (EOFError, KeyboardInterrupt):
        write("\n  (cancelled)\n")
        return WizardResult(provider=None, cancelled=True)
    if not model:
        write("  model id is required.\n")
        return WizardResult(provider=None, cancelled=True)

    write("  API key (Enter to skip — local endpoints like Ollama/vLLM don't need one):\n")
    try:
        api_key = read_line("  key> ").strip()
    except (EOFError, KeyboardInterrupt):
        api_key = ""

    name = derive_name(base_url, registry)
    try:
        provider = CustomProvider(
            name=name,
            base_url=base_url,
            model=model,
            api_key_inline=api_key,
        )
        registry.add(provider, overwrite=False)
        registry.set_default(name)
    except ValueError as e:
        write(f"  failed: {e}\n")
        return WizardResult(provider=None, cancelled=True)

    write(f"\n  saved as default provider: {name}\n")
    write(f"  endpoint: {base_url}\n")
    write(f"  model:    {model}\n")
    write("  change later with: phantom config provider use <name>\n")
    return WizardResult(provider=provider)
