"""``phantom doctor`` — host capability report.

Prints a quick status of every component the agent depends on:

* Python version.
* Phantom + omnicli package importability.
* Sandbox backends (probe + tier rank).
* Selected backend (the one that would be picked for the next call).
* Optional dependency footprint (which extras are installed).

Output is plain text by default; the ``--json`` flag emits a stable
machine-readable shape for the dashboard.

Examples
--------

>>> # In a real shell:
>>> # $ phantom doctor
>>> # Phantom doctor                             v4.0.0-dev (stage 0)
>>> #   ✓ python 3.11+               (3.13.12)
>>> #   ✓ phantom package            (importable)
>>> #   ...
"""

from __future__ import annotations

import json
import platform
import sys
from typing import Any

import typer

from phantom._version import __version__
from phantom.errors import SandboxUnavailableError
from phantom.sandbox.backends import all_backends
from phantom.sandbox.select import available_backends, select_backend


__all__ = ["build_report"]


def build_report() -> dict[str, Any]:
    """Return a machine-readable host report.

    Stable keys:

    * ``phantom_version``: str
    * ``python_version``:  str (X.Y.Z)
    * ``platform``:         str (linux / darwin / win32)
    * ``packages``:         {phantom: bool, omnicli: bool}
    * ``backends``:         list of {name, tier, available}
    * ``selected``:         str | None — the backend that would be picked
    """
    backends_info: list[dict[str, Any]] = []
    for b in all_backends():
        backends_info.append({
            "name": b.name,
            "tier": b.tier_rank,
            "available": b.probe(),
        })

    try:
        selected = select_backend().name
    except SandboxUnavailableError:
        selected = None

    omnicli_ok = True
    try:
        import omnicli  # noqa: F401
    except Exception:  # pragma: no cover — defensive
        omnicli_ok = False

    return {
        "phantom_version": __version__,
        "python_version": (
            f"{sys.version_info.major}."
            f"{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        ),
        "platform": sys.platform,
        "packages": {"phantom": True, "omnicli": omnicli_ok},
        "backends": backends_info,
        "selected": selected,
    }


def _print_text_report(report: dict[str, Any]) -> None:
    """Render the report as the human-readable text form."""
    typer.echo(f"Phantom doctor                          v{report['phantom_version']}")
    py_ok = sys.version_info >= (3, 11)
    mark = "✓" if py_ok else "✗"
    typer.echo(f"  {mark} python 3.11+               ({report['python_version']})")
    typer.echo("  ✓ phantom package            (importable)")
    omnicli_mark = "✓" if report["packages"]["omnicli"] else "✗"
    typer.echo(f"  {omnicli_mark} omnicli legacy package     ({'importable' if report['packages']['omnicli'] else 'broken'})")
    typer.echo("")
    typer.echo("  Sandbox backends:")
    for b in report["backends"]:
        bm = "✓" if b["available"] else "✗"
        typer.echo(f"    {bm} {b['name']:<20} (tier {b['tier']})")
    typer.echo("")
    if report["selected"] is None:
        typer.echo("  ✗ No sandbox available — install bubblewrap or firejail,")
        typer.echo("    or run on Linux ≥ 3.8 with user namespaces enabled.")
    else:
        typer.echo(f"  Selected sandbox: {report['selected']}")


# Note: the user-facing `doctor` Typer command lives in
# ``phantom/cli/__init__.py`` so its function signature (and Option(...)
# defaults) stay visible to Typer's reflection. This module exposes
# ``build_report`` and ``_print_text_report`` as the testable building
# blocks.
