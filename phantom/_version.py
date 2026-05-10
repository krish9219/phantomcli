"""Single-source version string for the phantom package.

Build pipelines and runtime introspection both read from here. Bumped manually
on release; CHANGELOG.md and version.json must agree.
"""

from __future__ import annotations

__all__ = ["__version__", "VERSION_TUPLE", "RELEASE_DATE"]

__version__: str = "1.1.31"

# Parsed form for comparisons. Pre-release suffixes are stripped here; use
# `packaging.version.Version` if you need full PEP 440 semantics.
VERSION_TUPLE: tuple[int, int, int] = (1, 1, 31)

# Stamp updated by the release pipeline. ISO-8601 date.
RELEASE_DATE: str = "2026-05-10"
