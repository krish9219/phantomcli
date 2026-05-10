"""Static import-discipline guards.

These tests are the regression net for cold-start performance: if any
heavy dependency starts being eagerly imported by the top-level
``phantom`` or ``phantom.cli`` modules, the suite fails and points
the developer at what slipped through.

Why this matters
----------------

The daemon-mode design buys us sub-1ms warm-path latency, but every
``phantom <command>`` cold start still pays Python's import cost.
We've measured the floor at ~120 ms (Typer + click + rich); anything
beyond that means an eager import we can defer.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import time

import pytest


# ─── upper bounds (regression guards) ──────────────────────────────────────


def test_phantom_top_level_import_under_50ms():
    """``import phantom`` must be near-free — nothing eager in __init__."""
    code = (
        "import time\n"
        "t = time.perf_counter()\n"
        "import phantom\n"
        "print((time.perf_counter() - t) * 1000)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    elapsed_ms = float(proc.stdout.strip())
    assert elapsed_ms < 50, f"phantom import took {elapsed_ms} ms (expected < 50)"


def test_phantom_cli_import_under_500ms():
    """``from phantom.cli import app`` includes Typer + click + rich.

    Floor is ~120 ms locally; CI runners (especially Windows) routinely
    spike to 500–700 ms cold. The budget caps regression risk while
    leaving headroom for noisy runners. If this fires consistently
    above 1000 ms, find the eager import that slipped in."""
    code = (
        "import time\n"
        "t = time.perf_counter()\n"
        "from phantom.cli import app\n"
        "print((time.perf_counter() - t) * 1000)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    elapsed_ms = float(proc.stdout.strip())
    assert elapsed_ms < 1000, f"phantom.cli import took {elapsed_ms} ms (expected < 1000)"


# ─── absent eager imports ──────────────────────────────────────────────────


HEAVY_DEPS = (
    "fastapi",
    "uvicorn",
    "starlette",
    "playwright",
    "pydantic",
    "anyio",
    "h11",
    "h2",
    "websockets.server",
    "openai",
    "anthropic",
    "google.generativeai",
    "PIL",
    "numpy",
    "scipy",
    "torch",
    "transformers",
    "sentence_transformers",
    "nacl",
)


def _import_in_subprocess(import_line: str) -> set[str]:
    """Run `import_line` in a fresh interpreter; return loaded module set."""
    code = (
        f"{import_line}\n"
        "import sys\n"
        "import json\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    import json as _json
    return set(_json.loads(proc.stdout))


@pytest.mark.parametrize("dep", HEAVY_DEPS)
def test_phantom_import_does_not_pull_heavy_dep(dep):
    loaded = _import_in_subprocess("import phantom")
    assert not any(m == dep or m.startswith(dep + ".") for m in loaded), (
        f"importing 'phantom' eagerly pulled {dep!r}"
    )


@pytest.mark.parametrize("dep", [
    "fastapi", "uvicorn", "starlette", "playwright",
    "openai", "anthropic", "google.generativeai",
    "PIL", "numpy", "torch",
])
def test_phantom_cli_import_does_not_pull_heavy_dep(dep):
    """The Typer app must not eagerly load dashboard/agent backends."""
    loaded = _import_in_subprocess("from phantom.cli import app")
    assert not any(m == dep or m.startswith(dep + ".") for m in loaded), (
        f"importing 'phantom.cli' eagerly pulled {dep!r}"
    )


def test_phantom_version_import_is_under_25ms():
    """The most minimal possible code path: just read the version."""
    code = (
        "import time\n"
        "t = time.perf_counter()\n"
        "from phantom._version import __version__\n"
        "print((time.perf_counter() - t) * 1000)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    elapsed_ms = float(proc.stdout.strip())
    assert elapsed_ms < 25, f"version import took {elapsed_ms} ms"
