"""ADR-0002 enforcement — `omnicli` is frozen.

This test compares the **public symbol surface** of every `omnicli.*`
module against a snapshot in `tests/_omnicli_public_snapshot.json`.
A diff means someone is trying to add behaviour to the frozen v3
package; ADR-0002 forbids that. New code goes in `phantom/`.

If the snapshot is genuinely outdated (e.g. a pure rename happened
during the v3 → v4 transition was approved), regenerate it with::

    python -c "import sys, os, ast, json; sys.path.insert(0, '.'); \
      modules = sorted(f[:-3] for f in os.listdir('omnicli') \
        if f.endswith('.py') and f != '__init__.py' and not f.startswith('_')); \
      out = {}; \
      [out.__setitem__(m, sorted({n.name for n in ast.parse(open(f'omnicli/{m}.py').read()).body \
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith('_')} \
        | {t.id for n in ast.parse(open(f'omnicli/{m}.py').read()).body \
            if isinstance(n, ast.Assign) for t in n.targets \
            if isinstance(t, ast.Name) and not t.id.startswith('_') and t.id.isupper()})) for m in modules]; \
      print(json.dumps(out, indent=2))" > tests/_omnicli_public_snapshot.json

But please open an ADR first explaining why a frozen surface needs to
move.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OMNICLI_DIR = REPO_ROOT / "omnicli"
SNAPSHOT_PATH = Path(__file__).resolve().parent / "_omnicli_public_snapshot.json"


def _public_names_of(path: Path) -> list[str]:
    """Return the top-level public names defined in *path*.

    "Public" = doesn't start with an underscore. We include functions,
    async functions, classes, and SCREAMING_SNAKE_CASE module-level
    constants. Lower-case module-level variables are intentionally
    excluded — they are usually private state, not public API.
    """
    names: set[str] = set()
    tree = ast.parse(path.read_text(), str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and not target.id.startswith("_")
                    and target.id.isupper()
                ):
                    names.add(target.id)
    return sorted(names)


def _current_surface() -> dict[str, list[str]]:
    surface: dict[str, list[str]] = {}
    for entry in sorted(os.listdir(OMNICLI_DIR)):
        if not entry.endswith(".py") or entry.startswith("_"):
            continue
        if entry == "__init__.py":
            continue
        module = entry[:-3]
        surface[module] = _public_names_of(OMNICLI_DIR / entry)
    return surface


@pytest.mark.stage0
@pytest.mark.security
def test_omnicli_public_surface_has_not_grown() -> None:
    """ADR-0002: no new public symbols may be added to `omnicli`."""
    expected = json.loads(SNAPSHOT_PATH.read_text())
    actual = _current_surface()

    # Allow EXACT match. Both directions matter:
    #   * extra modules in `actual` → growth, forbidden by ADR-0002.
    #   * extra modules in `expected` → a module was deleted, forbidden too.
    extra_modules = sorted(set(actual) - set(expected))
    missing_modules = sorted(set(expected) - set(actual))

    assert not extra_modules, (
        f"omnicli grew new public modules {extra_modules}. "
        "ADR-0002 forbids this. New code goes in phantom/."
    )
    assert not missing_modules, (
        f"omnicli lost modules {missing_modules}. "
        "ADR-0002 forbids removals from the frozen v3 surface."
    )

    for module, expected_names in expected.items():
        actual_names = actual[module]
        added = sorted(set(actual_names) - set(expected_names))
        removed = sorted(set(expected_names) - set(actual_names))

        assert not added, (
            f"omnicli.{module} grew new public symbols {added}. "
            "ADR-0002 forbids this. Move them to phantom/."
        )
        assert not removed, (
            f"omnicli.{module} lost public symbols {removed}. "
            "ADR-0002 forbids removals from v3 in a v4 development cycle."
        )
