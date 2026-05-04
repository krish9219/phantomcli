"""Plugin mirror — fetch signed plugin bundles from an HTTP index.

The mirror is split in two:

* :mod:`phantom.plugins.mirror.server` — a tiny FastAPI app that serves
  ``/index.json`` and ``/plugins/<name>/<version>.tar.gz``. Operators
  run it behind Caddy / nginx; for testing we spin one up on a random
  loopback port.
* :mod:`phantom.plugins.mirror.client` — fetches the index, resolves a
  name to a bundle, downloads it, verifies the bundle's manifest
  signature, extracts into ``$PHANTOM_HOME/plugins/<name>/``.

Bundle format
-------------

Plain tar.gz with at least ``manifest.json`` and ``__init__.py`` at the
archive root. Optional ``signature.txt`` with the base64-encoded Ed25519
signature over the bundle's SHA-256. The mirror's ``index.json`` carries
the SHA-256 digest publishers expect.
"""

from __future__ import annotations

from phantom.plugins.mirror.client import (
    MirrorClient,
    MirrorError,
    PluginEntry,
    PluginIndex,
)

__all__ = [
    "MirrorClient",
    "MirrorError",
    "PluginEntry",
    "PluginIndex",
]
