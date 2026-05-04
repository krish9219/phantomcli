"""In-package tests for phantom v4.

Two reasons we keep some tests inside the package itself:

1. Doctests live next to the code they test, so refactors don't silently
   skip them.
2. Stage-gate smoke tests (``test_stage_<N>_done.py``) act as machine-readable
   assertions that a stage is fully wired before we move on. They live next to
   the package so they ship with the wheel and CI never accidentally drops them.

Most behaviour tests still live in the top-level ``tests/`` directory — that's
the legacy convention and the existing 796-test baseline depends on it.
"""

from __future__ import annotations
