"""Phantom release pipeline — version stamps, audit checks, manifest.

The release pipeline is a callable Python function (``release()``)
that:

1. Verifies CHANGELOG.md has an entry for the current version.
2. Verifies every closed stage has a peer-review file with a sign-off.
3. Emits a release manifest (``dist/release.json``) the CDN reads.

Run with ``python -m phantom.release`` once Stage-8 wiring lands the
console-script entry. The function is also fully unit-testable.
"""

from __future__ import annotations

from phantom.release.pipeline import (
    ReleaseError,
    ReleaseManifest,
    audit_repo,
    build_manifest,
)

__all__ = [
    "ReleaseError",
    "ReleaseManifest",
    "audit_repo",
    "build_manifest",
]
