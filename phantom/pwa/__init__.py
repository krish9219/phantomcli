"""Phantom PWA — Progressive Web App assets.

The dashboard at ``phantom.aravindlabs.tech/app`` is served as static
files generated from this module. Stage 6 ships the manifest + service
worker source. The CLI command ``phantom pwa build`` (Stage 8) writes
them into a ``dist/pwa/`` directory the operator deploys behind Caddy.

This module is data-only: no runtime; tests just verify the strings
are well-formed.
"""

from __future__ import annotations

from phantom.pwa.build import build_pwa
from phantom.pwa.manifest import build_manifest, build_service_worker

__all__ = ["build_manifest", "build_pwa", "build_service_worker"]
