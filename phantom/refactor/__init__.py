"""Phantom refactor — symbol-aware code transformations.

Today
-----

* :mod:`phantom.refactor.python_rename` — rename a Python symbol with
  scope-awareness. Handles function-local vs module-global, respects
  shadowing, updates `from X import Y` references.

Out of scope this session
-------------------------

* JavaScript / TypeScript / Go / Rust renames. The right tool for those
  is tree-sitter (each language has its own grammar). Adding any single
  language is a session of its own; doing them all is a sprint.
* Type-aware moves, extract method, inline.

The Python rename is built so the user-facing tool surface
(``RefactorRequest`` → ``RefactorResult``) is language-agnostic. Adding
a TS rename later means adding ``phantom/refactor/ts_rename.py`` and
plugging it into the same dispatcher — not redesigning the tool API.
"""

from __future__ import annotations

from phantom.refactor.js_rename import (
    JsRefactorError,
    JsRefactorRequest,
    JsRefactorResult,
    JsRenamer,
    rename_js_symbol,
)
from phantom.refactor.python_rename import (
    PythonRenamer,
    RefactorError,
    RefactorRequest,
    RefactorResult,
    rename_python_symbol,
)

__all__ = [
    "JsRefactorError",
    "JsRefactorRequest",
    "JsRefactorResult",
    "JsRenamer",
    "PythonRenamer",
    "RefactorError",
    "RefactorRequest",
    "RefactorResult",
    "rename_js_symbol",
    "rename_python_symbol",
]
